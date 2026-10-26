/* Журнал группы: баллы 0–10 за работы, зашифрованные в journal.json.
 *
 * Сайт статический и публичный, репозиторий тоже, поэтому ФИО и баллы
 * хранятся только шифротекстом. В файле два «ящика» с одними и теми же
 * данными:
 *   view    — ключ из кода группы; его открывает любой, кто знает код;
 *   teacher — ключ из пароля преподавателя; внутри ещё и сам код группы,
 *             чтобы при сохранении заново зашифровать view.
 * Шифр — AES-256-GCM, ключ — PBKDF2-SHA256. Код группы — 12 знаков из
 * 31-символьного алфавита (около 59 бит): перебрать его по публичному
 * файлу нереально. Расшифрованные данные живут только в памяти вкладки.
 *
 * Сохранение — через GitHub API: файл ложится в ветку `journal`, а сайт
 * пересобирает workflow этой ветки (.github/workflows/journal.yml там же).
 * Токен вводит преподаватель, в журнал он не попадает.
 *
 * Вход преподавателя можно запомнить на устройстве на сутки или неделю.
 * Пароль при этом не хранится: в IndexedDB лежит неизвлекаемый ключ AES,
 * полученный из пароля, и срок. Ящик teacher сохраняется с той же солью,
 * пока пароль не сменится, поэтому этот ключ открывает и новые версии.
 *
 * Если на сайте включена сдача работ (submit_url в course.toml), в журнале
 * хранится и ключевая пара для неё: открытая половина — в обоих ящиках,
 * закрытая — только в ящике преподавателя. Сданные работы открываются
 * здесь же, в панели «Сданные работы». Само шифрование — в ../crypto.js.
 */
(function () {
  "use strict";

  const OWNER = "nikita-konkin";
  const REPO = "basic-linguistic-ml";
  const BRANCH = "journal";
  const FILE = "journal.json";
  const API = `https://api.github.com/repos/${OWNER}/${REPO}`;

  const FORMAT = mlgCrypto.JOURNAL_FORMAT;
  const ITERATIONS = mlgCrypto.ITERATIONS;
  const MIN_PASSWORD = 12;
  const MAX_SCORE = 10;
  // На случай, если страница открыта без сборки и списка работ курса в ней нет.
  const FALLBACK_WORKS = ["Л.р. № 1", "Л.р. № 2", "Л.р. № 3"];

  const { CODE_LENGTH, canonicalCode, formatCode, generateCode, toBase64, fromBase64 } = mlgCrypto;
  const seal = mlgCrypto.sealBox;
  const open = mlgCrypto.openBox;

  const KEY_CODE = "mlg-journal-code";
  const KEY_TOKEN = "mlg-journal-token";
  const DAY = 24 * 60 * 60 * 1000;

  const $ = (id) => document.getElementById(id);
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  const state = {
    mode: null, // "view" | "teacher"
    data: null, // {group, works, students: [{name, scores}]}
    code: null, // код группы, только у преподавателя
    teacherKey: null, // {key, salt}: ключ ящика teacher, пароль не хранится
    sha: null, // версия файла в ветке journal, если известна
    loadedText: null, // текст файла, с которого начата правка
    dirty: false,
    invalid: new Set(),
    recent: new Set(),
    pending: null, // ФИО из «Проверить список»
    submissions: null, // сданные работы: [{id, work, received, size, header, latest}]
    shown: [], // те из них, что сейчас в списке
    submitKey: null, // закрытый ключ сдачи, импортированный для WebCrypto
  };

  // Адрес приёма работ и работы курса; вставляются при сборке сайта.
  const config = readConfig();

  function readConfig() {
    const element = document.getElementById("mlg-config");
    try {
      const value = element ? JSON.parse(element.textContent) : {};
      return { submitUrl: (value.submitUrl || "").replace(/\/+$/, ""), works: value.works || [] };
    } catch (error) {
      return { submitUrl: "", works: [] };
    }
  }

  // ------------------------------------------------------------ хранилище

  function storageGet(store, key) {
    try {
      return window[store].getItem(key);
    } catch (error) {
      return null;
    }
  }

  function storageSet(store, key, value) {
    try {
      if (value === null) window[store].removeItem(key);
      else window[store].setItem(key, value);
    } catch (error) {
      /* приватный режим или запрет хранилища: просто не запоминаем */
    }
  }

  // ------------------------------------------------------ список ФИО

  // Ячейка-номер: «1», «1.», «1)», а в шапке таблицы — «№», «№ п/п».
  const NUMBER_CELL = /^(\d+\s*[.)]?|№(\s*п\/п)?|п\/п)$/i;
  // Шапка столбца с ФИО, если список скопирован из таблицы вместе с ней.
  const HEADER_CELL = /^(ф\.?\s*и\.?\s*о\.?(\s*студента)?|фамилия,?\s+имя,?\s+отчество|студент|студенты)$/i;

  // «1. Фамилия Имя», «1) …», «1 - …», строка из Excel через табуляцию.
  function parseNames(text) {
    const names = [];
    for (const line of (text || "").split(/\r?\n/)) {
      const cells = line.split("\t").map((cell) => cell.trim()).filter(Boolean);
      let cell = cells.find((value) => !NUMBER_CELL.test(value));
      if (!cell) continue;
      cell = cell.replace(/^\d+\s*[.)\-–—:]\s*/, "").replace(/^\d+\s+(?=\D)/, "");
      cell = cell.replace(/\s+/g, " ").trim();
      if (!cell || HEADER_CELL.test(cell)) continue;
      names.push(cell);
    }
    return names;
  }

  function nameKey(name) {
    return name.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
  }

  function sortStudents() {
    state.data.students.sort((a, b) => a.name.localeCompare(b.name, "ru"));
  }

  // ------------------------------------------------------------- вывод

  function setStatus(text, kind) {
    const box = $("status");
    box.textContent = text || "";
    box.className = "status" + (kind ? " " + kind : "");
  }

  // Кнопки с data-off="1" остаются выключенными и после занятости.
  function setBusy(busy) {
    document.querySelectorAll("button").forEach((button) => {
      button.disabled = busy || button.dataset.off === "1";
    });
  }

  function total(scores) {
    const marks = scores.filter((value) => typeof value === "number");
    return marks.length ? marks.reduce((sum, value) => sum + value, 0) : null;
  }

  function cell(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function iconButton(symbol, title, handler) {
    const button = cell("button", symbol, "icon");
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-label", title);
    button.addEventListener("click", handler);
    return button;
  }

  function renderTable() {
    const data = state.data;
    const teacher = state.mode === "teacher";
    const table = $("journal-table");
    table.replaceChildren();
    // Поля пересоздаются из данных, недопустимый ввод в них не переносится.
    state.invalid.clear();

    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.append(cell("th", "№", "num"), cell("th", "ФИО"));
    data.works.forEach((work) => headRow.append(cell("th", work, "work")));
    headRow.append(cell("th", "сумма", "num sum"));
    head.append(headRow);

    const body = document.createElement("tbody");
    data.students.forEach((student, row) => {
      const tr = document.createElement("tr");
      if (state.recent.has(nameKey(student.name))) tr.className = "added";
      const nameCell = cell("td", null, "name");
      nameCell.append(document.createTextNode(student.name));
      if (teacher) {
        nameCell.append(
          iconButton("✎", `Изменить ФИО: ${student.name}`, () => renameStudent(student)),
          iconButton("×", `Удалить: ${student.name}`, () => deleteStudent(student)),
        );
      }
      tr.append(cell("td", String(row + 1), "num"), nameCell);

      const sumCell = cell("td", null, "num sum");
      const showSum = () => {
        const value = total(student.scores);
        sumCell.textContent = value === null ? "—" : String(value);
      };

      data.works.forEach((work, column) => {
        const value = student.scores[column];
        const td = cell("td", null, "num score");
        td.dataset.student = nameKey(student.name);
        td.dataset.column = String(column);
        const handed = student.handed[column];
        const showHanded = () => {
          td.classList.toggle("pending", Boolean(handed) && typeof student.scores[column] !== "number");
        };
        if (handed) td.title = `Сдано ${when(handed)}`;
        if (teacher) {
          const input = document.createElement("input");
          input.type = "number";
          input.min = "0";
          input.max = String(MAX_SCORE);
          input.step = "1";
          input.inputMode = "numeric";
          input.value = typeof value === "number" ? String(value) : "";
          input.setAttribute("aria-label", `${student.name}, ${work}`);
          const id = `${row}:${column}`;
          input.addEventListener("input", () => {
            const text = input.value.trim();
            const ok = !input.validity.badInput && (text === "" || (/^\d{1,2}$/.test(text) && Number(text) <= MAX_SCORE));
            input.setAttribute("aria-invalid", ok ? "false" : "true");
            if (ok) {
              state.invalid.delete(id);
              student.scores[column] = text === "" ? null : Number(text);
              showSum();
              showHanded();
            } else {
              state.invalid.add(id);
            }
            markDirty();
          });
          td.append(input);
        } else {
          td.textContent = typeof value === "number" ? String(value) : handed ? "сдано" : "—";
        }
        showHanded();
        tr.append(td);
      });
      showSum();
      tr.append(sumCell);
      body.append(tr);
    });

    table.append(head, body);
    const count = data.students.length;
    const pending = data.students.some((student) => student.handed.some(Boolean));
    $("table-note").textContent = count
      ? `Студентов: ${count}. «—» — работа ещё не оценена. Баллы — от 0 до ${MAX_SCORE}.` +
        (pending ? " Цветом отмечены работы, которые сданы и ждут проверки." : "")
      : teacher
        ? "Студентов пока нет — добавьте список ниже."
        : "Студентов пока нет.";
    $("subtitle").textContent = data.group
      ? `Группа ${data.group}. Баллы за лабораторные работы, от 0 до ${MAX_SCORE}.`
      : `Баллы за лабораторные работы, от 0 до ${MAX_SCORE}.`;
    $("table-section").hidden = false;
    if (teacher) {
      renderWorks();
      renderMarks();
    }
  }

  // Список столбцов в панели «Работы»: название, порядок, удаление.
  function renderWorks() {
    const works = state.data.works;
    const list = $("works-list");
    list.replaceChildren();
    works.forEach((work, index) => {
      const li = document.createElement("li");
      const input = document.createElement("input");
      input.type = "text";
      input.value = work;
      input.setAttribute("aria-label", `Название столбца ${index + 1}`);
      input.addEventListener("change", () => renameWork(index, input));
      const up = iconButton("↑", `Переставить «${work}» левее`, () => moveWork(index, -1));
      const down = iconButton("↓", `Переставить «${work}» правее`, () => moveWork(index, 1));
      up.classList.add("up");
      down.classList.add("down");
      li.append(input, up, down, iconButton("×", `Удалить столбец «${work}»`, () => deleteWork(index)));
      if (config.submitUrl) {
        const select = document.createElement("select");
        select.setAttribute("aria-label", `Какая работа сдаётся в столбец «${work}»`);
        select.append(new Option("сдача: не связан с работой курса", ""));
        for (const item of config.works) select.append(new Option(`сдача: ${item.title}`, item.slug));
        select.value = state.data.workSlugs[index] || "";
        select.addEventListener("change", () => {
          state.data.workSlugs[index] = select.value;
          syncHanded();
          markDirty();
          renderTable();
        });
        li.append(select);
      }
      list.append(li);
    });
    $("works-count").textContent = works.length
      ? `Столбцов: ${works.length}.`
      : "Столбцов пока нет — добавьте хотя бы одну работу.";
  }

  function courseWorks() {
    const names = [...document.querySelectorAll("#course-works option")].map((option) => option.value.trim());
    return names.filter(Boolean).length ? names.filter(Boolean) : FALLBACK_WORKS.slice();
  }

  function markDirty() {
    state.dirty = true;
  }

  // ---------------------------------------------------- загрузка файла

  async function fetchSiteCopy() {
    const response = await fetch(FILE, { cache: "no-store" });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`сайт ответил ${response.status}`);
    const text = await response.text();
    return { text, file: JSON.parse(text), sha: null };
  }

  function token() {
    return storageGet("localStorage", KEY_TOKEN) || storageGet("sessionStorage", KEY_TOKEN);
  }

  function github(path, options = {}) {
    return fetch(`${API}${path}`, {
      ...options,
      cache: "no-store",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token()}`,
        "X-GitHub-Api-Version": "2022-11-28",
        ...(options.headers || {}),
      },
    });
  }

  async function fetchRepoCopy() {
    const response = await github(`/contents/${FILE}?ref=${BRANCH}`);
    if (response.status === 404) return null;
    if (!response.ok) throw githubError(response.status, "чтение журнала");
    const meta = await response.json();
    const text = decoder.decode(fromBase64(meta.content));
    return { text, file: JSON.parse(text), sha: meta.sha };
  }

  function githubError(status, what) {
    const reasons = {
      401: "GitHub не принял токен — проверьте его или создайте новый",
      403: "у токена не хватает прав",
      404: "репозиторий или ветка journal не найдены, либо у токена нет к ним доступа",
      409: "файл изменился, пока вы его правили",
      422: "GitHub отклонил запрос",
    };
    return new Error(`${what}: ${reasons[status] || "ошибка " + status}`);
  }

  function checkFile(file) {
    if (!file || file.format !== FORMAT || !file.view || !file.teacher || !file.kdf) {
      throw new Error("файл журнала повреждён или другой версии");
    }
  }

  function checkData(data) {
    if (!data || !Array.isArray(data.works) || !Array.isArray(data.students)) {
      throw new Error("внутри журнала неожиданные данные");
    }
    data.group = data.group || "";
    // Какая работа курса сдаётся в столбец (slug или ""); в старых журналах
    // поля нет — угадываем по названию столбца.
    const slugs = Array.isArray(data.workSlugs) ? data.workSlugs : [];
    data.workSlugs = data.works.map((work, index) =>
      typeof slugs[index] === "string" ? slugs[index] : guessWork(work),
    );
    data.students.forEach((student) => {
      student.scores = data.works.map((_, index) => {
        const value = student.scores ? student.scores[index] : null;
        return typeof value === "number" ? value : null;
      });
      // Отметка о сдаче: время последней версии, null — не сдано.
      student.handed = data.works.map((_, index) => {
        const value = student.handed ? student.handed[index] : null;
        return typeof value === "string" ? value : null;
      });
    });
    return data;
  }

  // ------------------------------------------------------- режим группы

  async function openView(input, remember) {
    const code = canonicalCode(input);
    if (code.length !== CODE_LENGTH) {
      setStatus(`В коде группы ${CODE_LENGTH} знаков, например ABCD-EFGH-JKMN. Проверьте, всё ли набрано.`, "error");
      return;
    }
    setBusy(true);
    setStatus("Расшифровываю…");
    try {
      const copy = await fetchSiteCopy();
      if (!copy) {
        setStatus("Журнал ещё не опубликован.", "error");
        return;
      }
      checkFile(copy.file);
      const data = await open(copy.file, code, "view");
      if (!data) {
        setStatus("Код не подходит. Проверьте его у преподавателя.", "error");
        return;
      }
      state.mode = "view";
      state.data = checkData(data);
      sortStudents();
      if (remember) storageSet("localStorage", KEY_CODE, code);
      $("view-code").value = formatCode(code);
      $("open-view").hidden = true;
      renderTable();
      setStatus("");
    } catch (error) {
      setStatus(`Не удалось открыть журнал: ${error.message}.`, "error");
    } finally {
      setBusy(false);
    }
  }

  function closeView() {
    storageSet("localStorage", KEY_CODE, null);
    state.mode = null;
    state.data = null;
    $("journal-table").replaceChildren();
    $("table-section").hidden = true;
    $("open-view").hidden = false;
    $("view-code").value = "";
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
    setStatus("");
  }

  // ------------------------------------------------- режим преподавателя

  async function loadForTeacher() {
    if (token()) {
      try {
        return await fetchRepoCopy();
      } catch (error) {
        setStatus(`${error.message}. Беру опубликованную копию.`, "error");
      }
    }
    return fetchSiteCopy();
  }

  // Ключ ящика teacher из пароля: с солью из файла, а если файла нет или он
  // зашифрован с другим числом итераций — с новой солью.
  async function keyFromPassword(password, sealed, iterations) {
    const secret = password.normalize("NFC");
    if (sealed && iterations === ITERATIONS) {
      const salt = fromBase64(sealed.salt);
      return { key: await mlgCrypto.deriveBoxKey(secret, salt), salt };
    }
    const salt = mlgCrypto.newSalt();
    return { key: await mlgCrypto.deriveBoxKey(secret, salt), salt };
  }

  async function teacherLogin(password) {
    if (!password) return;
    setBusy(true);
    setStatus("Загружаю журнал…");
    try {
      const copy = await loadForTeacher();
      if (!copy) {
        setStatus("Журнала ещё нет — создайте его.", "");
        $("teacher-form").hidden = true;
        $("create-form").hidden = false;
        return;
      }
      checkFile(copy.file);
      setStatus("Расшифровываю…");
      // Ключ из пароля и соли файла открывает ящик и потом же его сохраняет.
      const teacherKey = await keyFromPassword(password, copy.file.teacher, copy.file.kdf.iterations);
      const sameSalt = toBase64(teacherKey.salt) === copy.file.teacher.salt;
      const payload = sameSalt
        ? await mlgCrypto.openBoxWith(copy.file.teacher, teacherKey.key, "teacher")
        : await open(copy.file, password.normalize("NFC"), "teacher");
      if (!payload) {
        setStatus("Неверный пароль преподавателя.", "error");
        return;
      }
      const days = Number($("teacher-remember").value);
      let note = "";
      if (days > 0) {
        const until = await rememberSession(teacherKey, Date.now() + days * DAY);
        note = until ? ` Вход запомнен на этом устройстве до ${until}.` : "";
      } else {
        await forgetSession();
      }
      await enterTeacher(payload, teacherKey, copy, note);
    } catch (error) {
      setStatus(`Не удалось открыть журнал: ${error.message}.`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function enterTeacher(payload, teacherKey, copy, note) {
    const code = payload.code;
    delete payload.code;
    startTeacher(checkData(payload), code, teacherKey, copy);
    if (await ensureSubmitKeys()) {
      setStatus(
        `Журнал открыт.${note} Для приёма работ создан ключ — нажмите «Сохранить», чтобы страница сдачи заработала.`,
        "ok",
      );
    } else {
      setStatus(`Журнал открыт.${note} Изменения сохраняются кнопкой «Сохранить».`, "ok");
    }
    loadSubmissions();
  }

  // ------------------------------------------- запомненный вход (IndexedDB)

  function sessionStore(mode, action) {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open("mlg-journal", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("session");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const db = request.result;
        const transaction = db.transaction("session", mode);
        const result = action(transaction.objectStore("session"));
        transaction.oncomplete = () => {
          db.close();
          resolve(result.result);
        };
        transaction.onerror = () => reject(transaction.error);
      };
    });
  }

  async function loadSession() {
    try {
      return (await sessionStore("readonly", (store) => store.get("teacher"))) || null;
    } catch (error) {
      return null;
    }
  }

  function untilText(time) {
    return new Date(time).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  }

  // Возвращает «до когда» или null, если браузер не дал сохранить ключ.
  async function rememberSession(teacherKey, expires) {
    try {
      await sessionStore("readwrite", (store) =>
        store.put({ key: teacherKey.key, salt: toBase64(teacherKey.salt), expires }, "teacher"),
      );
      return untilText(expires);
    } catch (error) {
      return null;
    }
  }

  async function forgetSession() {
    try {
      await sessionStore("readwrite", (store) => store.delete("teacher"));
    } catch (error) {
      /* хранилище недоступно — забывать нечего */
    }
  }

  // После смены пароля и сохранения запомненный ключ заменяется новым,
  // срок остаётся прежним.
  async function refreshSession() {
    const saved = await loadSession();
    if (saved && saved.expires > Date.now() && saved.salt !== toBase64(state.teacherKey.salt)) {
      await rememberSession(state.teacherKey, saved.expires);
    }
  }

  async function resumeTeacher() {
    const saved = await loadSession();
    if (!saved) return false;
    if (!(saved.expires > Date.now()) || !saved.key) {
      await forgetSession();
      return false;
    }
    setBusy(true);
    setStatus("Открываю журнал преподавателя…");
    try {
      const copy = await loadForTeacher();
      if (!copy) {
        setStatus("");
        return false;
      }
      checkFile(copy.file);
      const payload =
        copy.file.teacher.salt === saved.salt ? await mlgCrypto.openBoxWith(copy.file.teacher, saved.key, "teacher") : null;
      if (!payload) {
        await forgetSession();
        $("teacher-entry").open = true;
        setStatus("Пароль преподавателя сменили — войдите заново.", "error");
        return true;
      }
      await enterTeacher(payload, { key: saved.key, salt: fromBase64(saved.salt) }, copy, ` Вход запомнен до ${untilText(saved.expires)}.`);
      return true;
    } catch (error) {
      setStatus(`Не удалось открыть журнал: ${error.message}.`, "error");
      return true;
    } finally {
      setBusy(false);
    }
  }

  function startTeacher(data, code, teacherKey, copy) {
    state.mode = "teacher";
    state.data = data;
    state.code = code;
    state.teacherKey = teacherKey;
    state.sha = copy ? copy.sha : null;
    state.loadedText = copy ? copy.text : null;
    state.dirty = !copy;
    sortStudents();
    $("teacher-password").value = "";
    $("teacher-entry").hidden = true;
    $("open-view").hidden = true;
    $("btn-close").hidden = true;
    $("teacher-panel").hidden = false;
    $("group-name").value = data.group;
    $("submissions-panel").hidden = !config.submitUrl;
    showTokenState();
    renderTable();
  }

  async function createJournal(group, password, again) {
    if (password.length < MIN_PASSWORD) {
      setStatus(`Пароль слишком короткий: нужно не меньше ${MIN_PASSWORD} знаков.`, "error");
      return;
    }
    if (password !== again) {
      setStatus("Пароли не совпадают.", "error");
      return;
    }
    const data = checkData({ group: group.trim(), works: courseWorks(), students: [] });
    startTeacher(data, generateCode(), await keyFromPassword(password, null, ITERATIONS), null);
    showCode();
    setStatus("Журнал создан. Добавьте студентов и нажмите «Сохранить» — до этого он нигде не хранится.", "ok");
    ensureSubmitKeys();
  }

  function showCode() {
    const formatted = formatCode(state.code);
    const link = `${location.origin}${location.pathname}#${formatted}`;
    $("code-value").textContent = formatted;
    $("code-link").textContent = link;
    $("code-link").href = link;
    $("code-box").hidden = false;
  }

  async function copyText(text, what) {
    try {
      await navigator.clipboard.writeText(text);
      setStatus(`${what} скопирован${what === "Код" ? "" : "а"}.`, "ok");
    } catch (error) {
      setStatus(`Скопируйте вручную: ${text}`, "");
    }
  }

  function newCode() {
    const ok = confirm(
      "Сгенерировать новый код группы?\n\nПосле сохранения старый код перестанет открывать журнал, " +
        "и новый код нужно будет сообщить группе. Старые версии файла в истории репозитория " +
        "по-прежнему открываются старым кодом.",
    );
    if (!ok) return;
    state.code = generateCode();
    markDirty();
    showCode();
    setStatus("Код изменён. Нажмите «Сохранить», чтобы он начал действовать.", "ok");
  }

  async function changePassword(password, again) {
    if (password.length < MIN_PASSWORD) {
      setStatus(`Пароль слишком короткий: нужно не меньше ${MIN_PASSWORD} знаков.`, "error");
      return;
    }
    if (password !== again) {
      setStatus("Пароли не совпадают.", "error");
      return;
    }
    // Новая соль: старый ключ, запомненный на других устройствах, не подойдёт.
    state.teacherKey = await keyFromPassword(password, null, ITERATIONS);
    markDirty();
    $("new-password").value = "";
    $("new-password2").value = "";
    setStatus("Пароль изменён. Нажмите «Сохранить», чтобы он начал действовать.", "ok");
  }

  // Правка журнала.

  function previewNames() {
    const names = parseNames($("students-input").value);
    const known = new Set(state.data.students.map((student) => nameKey(student.name)));
    const fresh = [];
    const list = $("preview-list");
    list.replaceChildren();
    for (const name of names) {
      const key = nameKey(name);
      const li = cell("li", name);
      if (known.has(key)) {
        li.className = "dup";
        li.title = "уже есть в журнале";
      } else {
        known.add(key);
        fresh.push(name);
      }
      list.append(li);
    }
    state.pending = fresh;
    const repeats = names.length - fresh.length;
    $("preview-summary").textContent = names.length
      ? `Будет добавлено: ${fresh.length}. Уже есть в журнале или повторяется в списке: ${repeats}.`
      : "В списке не нашлось ни одного ФИО.";
    $("btn-add").disabled = fresh.length === 0;
    $("preview").hidden = false;
  }

  function addPending() {
    const names = state.pending || [];
    for (const name of names) {
      state.data.students.push({ name, scores: state.data.works.map(() => null), handed: state.data.works.map(() => null) });
      state.recent.add(nameKey(name));
    }
    sortStudents();
    state.pending = null;
    $("students-input").value = "";
    $("preview").hidden = true;
    markDirty();
    renderTable();
    setStatus(`Добавлено студентов: ${names.length}. Не забудьте сохранить.`, "ok");
  }

  function renameStudent(student) {
    const name = (prompt("ФИО", student.name) || "").replace(/\s+/g, " ").trim();
    if (!name || name === student.name) return;
    const clash = state.data.students.some((other) => other !== student && nameKey(other.name) === nameKey(name));
    if (clash) {
      setStatus(`«${name}» уже есть в журнале.`, "error");
      return;
    }
    student.name = name;
    sortStudents();
    syncHanded();
    markDirty();
    renderTable();
  }

  function deleteStudent(student) {
    if (!confirm(`Удалить из журнала: ${student.name}?\nЕго баллы тоже удалятся.`)) return;
    state.data.students = state.data.students.filter((other) => other !== student);
    markDirty();
    renderTable();
  }

  function addWork(name) {
    name = name.replace(/\s+/g, " ").trim();
    if (!name) return;
    if (state.data.works.includes(name)) {
      setStatus(`Столбец «${name}» уже есть.`, "error");
      return;
    }
    state.data.works.push(name);
    state.data.workSlugs.push(guessWork(name));
    state.data.students.forEach((student) => {
      student.scores.push(null);
      student.handed.push(null);
    });
    syncHanded();
    $("work-name").value = "";
    markDirty();
    renderTable();
    setStatus(`Столбец «${name}» добавлен в конец таблицы. Не забудьте сохранить.`, "ok");
  }

  function renameWork(index, input) {
    const works = state.data.works;
    const name = input.value.replace(/\s+/g, " ").trim();
    if (!name || works.some((other, i) => i !== index && other === name)) {
      setStatus(name ? `Столбец «${name}» уже есть.` : "Название столбца не может быть пустым.", "error");
      input.value = works[index];
      return;
    }
    if (name === works[index]) return;
    works[index] = name;
    if (!state.data.workSlugs[index]) {
      state.data.workSlugs[index] = guessWork(name);
      syncHanded();
    }
    markDirty();
    renderTable();
  }

  // Столбец меняется местами с соседним, баллы переезжают вместе с ним.
  function moveWork(index, step) {
    const works = state.data.works;
    const target = index + step;
    if (target < 0 || target >= works.length) return;
    const swap = (values) => {
      [values[index], values[target]] = [values[target], values[index]];
    };
    swap(works);
    swap(state.data.workSlugs);
    state.data.students.forEach((student) => {
      swap(student.scores);
      swap(student.handed);
    });
    markDirty();
    renderTable();
    // Фокус остаётся на той же стрелке, чтобы двигать столбец дальше с
    // клавиатуры; у края, где этой стрелки нет, — на обратной.
    const moved = $("works-list").children[target];
    const atEdge = step < 0 ? target === 0 : target === works.length - 1;
    const same = step < 0 ? ".up" : ".down";
    const other = step < 0 ? ".down" : ".up";
    moved.querySelector(atEdge ? other : same).focus();
  }

  function deleteWork(index) {
    const work = state.data.works[index];
    const scored = state.data.students.filter((student) => typeof student.scores[index] === "number").length;
    const warning = scored ? `\nВ нём баллов: ${scored} — они удалятся.` : "";
    if (!confirm(`Удалить столбец «${work}»?${warning}`)) return;
    state.data.works.splice(index, 1);
    state.data.workSlugs.splice(index, 1);
    state.data.students.forEach((student) => {
      student.scores.splice(index, 1);
      student.handed.splice(index, 1);
    });
    markDirty();
    renderTable();
  }

  // Сохранение.

  async function buildFile() {
    const data = {
      group: state.data.group,
      works: state.data.works,
      workSlugs: state.data.workSlugs,
      students: state.data.students,
      submitKey: state.data.submitKey, // открытый: им шифрует страница сдачи
    };
    const file = {
      format: FORMAT,
      kdf: { name: "PBKDF2", hash: "SHA-256", iterations: ITERATIONS },
      view: await seal(data, state.code, "view"),
      teacher: await mlgCrypto.sealBoxWith(
        { ...data, code: state.code, submitPrivate: state.data.submitPrivate },
        state.teacherKey.key,
        state.teacherKey.salt,
        "teacher",
      ),
    };
    return JSON.stringify(file, null, 1) + "\n";
  }

  function saveFile(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function download(text) {
    saveFile(new Blob([text], { type: "application/json" }), FILE);
  }

  function checkScores() {
    if (state.invalid.size) {
      setStatus(`Исправьте выделенные баллы: допускаются целые числа от 0 до ${MAX_SCORE}.`, "error");
      return false;
    }
    return true;
  }

  async function save() {
    if (!checkScores()) return;
    setBusy(true);
    setStatus("Шифрую…");
    let text = null;
    try {
      text = await buildFile();
      if (!token()) {
        download(text);
        state.dirty = false;
        await refreshSession();
        setStatus(
          "Токен не задан, поэтому файл скачан. Загрузите его в ветку journal репозитория " +
            "(GitHub → ветка journal → Add file → Upload files) под любым именем — " +
            "сайт пересоберётся сам, группа увидит изменения через 1–2 минуты.",
          "ok",
        );
        return;
      }

      setStatus("Проверяю версию на GitHub…");
      const current = await fetchRepoCopy();
      if (current) {
        const sameAsLoaded = state.sha ? current.sha === state.sha : current.text === state.loadedText;
        if (!sameAsLoaded) {
          download(text);
          setStatus(
            state.loadedText === null
              ? "В репозитории уже есть журнал. Войдите в него паролем вместо создания нового. Ваш вариант на всякий случай скачан."
              : "Журнал изменили с другого устройства после того, как вы его открыли. Ваш вариант скачан; войдите заново, чтобы увидеть свежую версию.",
            "error",
          );
          return;
        }
      }

      setStatus("Сохраняю на GitHub…");
      const body = { message: "Журнал: обновление", content: toBase64(encoder.encode(text)), branch: BRANCH };
      if (current) body.sha = current.sha;
      const put = await github(`/contents/${FILE}`, { method: "PUT", body: JSON.stringify(body) });
      if (!put.ok) throw githubError(put.status, "запись журнала");
      const result = await put.json();
      state.sha = result.content.sha;
      state.loadedText = text;
      state.dirty = false;
      await refreshSession();
      state.recent.clear();
      renderTable();
      // Сайт пересобирает workflow ветки journal — по самой записи файла.
      setStatus("Сохранено. Группа увидит изменения на сайте через 1–2 минуты.", "ok");
    } catch (error) {
      if (text) download(text);
      setStatus(`Не удалось сохранить: ${error.message}.${text ? " Ваш вариант скачан файлом." : ""}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function downloadOnly() {
    if (!checkScores()) return;
    setBusy(true);
    setStatus("Шифрую…");
    try {
      download(await buildFile());
      setStatus("Файл скачан.", "ok");
    } finally {
      setBusy(false);
    }
  }

  // Сданные работы.

  // Ключ для приёма работ создаётся при первом входе после включения сдачи.
  // Группа получит его открытую половину, когда журнал будет сохранён.
  async function ensureSubmitKeys() {
    if (!config.submitUrl || state.data.submitPrivate) return false;
    const keys = await mlgCrypto.generateSubmitKeys();
    state.data.submitKey = keys.publicKey;
    state.data.submitPrivate = keys.privateKey;
    markDirty();
    return true;
  }

  async function submitPrivateKey() {
    if (!state.submitKey) state.submitKey = await mlgCrypto.importPrivate(state.data.submitPrivate);
    return state.submitKey;
  }

  function workTitle(slug) {
    const work = config.works.find((item) => item.slug === slug);
    return work ? work.journal || work.title : slug;
  }

  function titleKey(text) {
    return (text || "").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
  }

  // Догадка, какая работа курса сдаётся в столбец: в названии столбца есть
  // её название из журнала (поле journal в course.toml); если подходят
  // несколько («Классификация» и «Классификация текстов») — самое длинное.
  // Преподаватель может поправить выбор в панели столбцов.
  function guessWork(column) {
    const name = titleKey(column);
    let best = null;
    for (const work of config.works) {
      const title = titleKey(work.journal);
      if (title && name.includes(title) && (!best || title.length > titleKey(best.journal).length)) best = work;
    }
    return best ? best.slug : "";
  }

  function columnWorks() {
    return state.data.workSlugs;
  }

  // Отметки «сдано» в журнале — по последним версиям на сервере приёма.
  // Они сохраняются в журнал, чтобы их видела и группа.
  function syncHanded() {
    if (!state.submissions) return 0;
    const latest = new Map();
    for (const item of state.submissions) {
      if (item.header) latest.set(`${nameKey(item.header.name)}|${item.work}`, item.received);
    }
    let changed = 0;
    for (const student of state.data.students) {
      state.data.workSlugs.forEach((slug, column) => {
        const value = (slug && latest.get(`${nameKey(student.name)}|${slug}`)) || null;
        if (student.handed[column] !== value) {
          student.handed[column] = value;
          changed += 1;
        }
      });
    }
    return changed;
  }

  function when(iso) {
    return new Date(iso).toLocaleString("ru-RU", {
      timeZone: "Europe/Moscow",
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function sizeText(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} МБ`;
  }

  function checkText(check) {
    if (!check || !check.cells) return "";
    const parts = [check.executed === check.cells ? "все ячейки выполнены" : `выполнено ${check.executed} из ${check.cells}`];
    if (check.errors) parts.push(`ячеек с ошибкой: ${check.errors}`);
    if (check.heading === "other") parts.push(`заголовок работы «${workTitle(check.headingWork)}»`);
    if (check.heading === "unknown") parts.push("заголовок не узнан");
    return parts.join(" · ");
  }

  function setSubmissionsStatus(text, kind) {
    const box = $("submissions-status");
    box.textContent = text;
    box.className = "small" + (kind ? " " + kind : "");
  }

  async function loadSubmissions() {
    if (!config.submitUrl || !state.data.submitPrivate) return;
    setSubmissionsStatus("Загружаю список…");
    try {
      const response = await fetch(`${config.submitUrl}/api/submissions`, { cache: "no-store" });
      if (!response.ok) throw new Error(`сервер ответил ${response.status}`);
      const listing = await response.json();
      const key = await submitPrivateKey();
      const items = [];
      for (const item of listing) {
        const opened = await mlgCrypto.openSubmissionHead(key, item.work, fromBase64(item.head || ""));
        const header = opened && opened.header.work === item.work ? opened.header : null;
        items.push({ ...item, header, latest: false });
      }
      // Список идёт по времени, поэтому последняя версия — последняя в нём.
      const last = new Map();
      for (const item of items) if (item.header) last.set(`${nameKey(item.header.name)}|${item.work}`, item);
      last.forEach((item) => {
        item.latest = true;
      });
      state.submissions = items;
      const changed = syncHanded();
      if (changed) {
        markDirty();
        renderTable();
      }
      renderSubmissions();
      renderMarks();
      if (changed) {
        setSubmissionsStatus(
          `${$("submissions-status").textContent} Отметки о сдаче в журнале обновлены — нажмите «Сохранить», чтобы их увидела группа.`,
          "ok",
        );
      }
    } catch (error) {
      setSubmissionsStatus(`Не удалось загрузить сданные работы: ${error.message}. Сервер приёма: ${config.submitUrl}`, "error");
    }
  }

  function renderSubmissions() {
    const items = state.submissions || [];
    const work = $("submissions-work").value;
    const latestOnly = $("submissions-latest").checked;
    const known = new Set(state.data.students.map((student) => nameKey(student.name)));
    state.shown = items
      .filter((item) => (!work || item.work === work) && (!latestOnly || item.latest || !item.header))
      .reverse();
    const list = $("submissions-list");
    list.replaceChildren();
    for (const item of state.shown) {
      const li = cell("li", null, item.latest ? "" : "old");
      li.dataset.id = item.id;
      const text = cell("div", null, "text");
      const button = cell("button", "скачать");
      button.type = "button";
      if (item.header) {
        const who = cell("div", null, "who");
        who.append(cell("b", item.header.name));
        if (!known.has(nameKey(item.header.name))) who.append(" ", cell("span", "нет в журнале", "flag"));
        if (!item.latest) who.append(" ", cell("span", "старая версия", "flag quiet"));
        const what =
          item.header.kind === "text"
            ? `${workTitle(item.work)} · текстовый ответ`
            : `${workTitle(item.work)} · ${item.header.filename} · ${sizeText(item.header.size)}`;
        text.append(
          who,
          cell("div", what, "what"),
          cell("div", [when(item.received), checkText(item.header.check)].filter(Boolean).join(" · "), "meta"),
        );

        button.setAttribute("aria-label", `Скачать: ${item.header.name}, ${item.header.filename}`);
        button.addEventListener("click", () => downloadSubmission(item));
      } else {
        li.className = "broken";
        text.append(
          cell("div", "не расшифровывается", "who"),
          cell("div", `${workTitle(item.work)} · ${when(item.received)} · ${item.id}`, "meta"),
        );
        button.disabled = true;
        button.dataset.off = "1";
      }
      li.append(text, button);
      if (item.header && item.header.kind === "text") li.append(answerBlock(item));
      list.append(li);
    }
    const people = new Set(items.filter((item) => item.header).map((item) => `${nameKey(item.header.name)}|${item.work}`));
    setSubmissionsStatus(
      items.length
        ? `Отправок: ${items.length}, сданных работ (последние версии): ${people.size}. В списке: ${state.shown.length}.`
        : "Пока никто ничего не сдал.",
    );
    const zip = $("btn-submissions-zip");
    zip.dataset.off = state.shown.some((item) => item.header) ? "" : "1";
    zip.disabled = zip.dataset.off === "1";
  }

  // «сдано» в ячейке журнала: есть сдача этого студента по работе столбца.
  function renderMarks() {
    document.querySelectorAll("#journal-table .handed").forEach((mark) => mark.remove());
    if (state.mode !== "teacher" || !state.submissions) return;
    const slugs = columnWorks();
    const found = new Map();
    for (const item of state.submissions) {
      if (!item.header) continue;
      const key = `${nameKey(item.header.name)}|${item.work}`;
      const entry = found.get(key) || { count: 0 };
      entry.count += 1;
      entry.item = item; // список идёт по времени: в конце — последняя версия
      found.set(key, entry);
    }
    document.querySelectorAll("#journal-table td.score").forEach((td) => {
      const slug = slugs[Number(td.dataset.column)];
      const entry = slug && found.get(`${td.dataset.student}|${slug}`);
      if (!entry) return;
      const mark = cell("button", "сдано", "handed");
      mark.type = "button";
      const textual = entry.item.header.kind === "text";
      mark.title =
        `Сдано ${when(entry.item.received)}${entry.count > 1 ? `, версий: ${entry.count}` : ""}. ` +
        (textual ? "Нажмите, чтобы посмотреть ответ." : "Нажмите, чтобы скачать.");
      mark.addEventListener("click", () => (textual ? revealSubmission(entry.item) : downloadSubmission(entry.item)));
      td.append(mark);
    });
  }

  // Текстовый ответ: ссылки кликабельны, остальное — как есть. Текст пишет
  // студент, поэтому ссылками становятся только http(s)-адреса, и в DOM он
  // попадает только как текст.
  const LINK = /https?:\/\/[^\s<>"«»]+[^\s<>"«».,;:!?)]/g;

  function linkified(text) {
    const box = cell("div", null, "answer");
    let last = 0;
    for (const match of text.matchAll(LINK)) {
      box.append(text.slice(last, match.index));
      const link = cell("a", match[0]);
      link.href = match[0];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      box.append(link);
      last = match.index + match[0].length;
    }
    box.append(text.slice(last));
    return box;
  }

  // Длинный ответ свёрнут до нескольких строк; целиком он лежит в конверте,
  // в заголовке — только начало (header.truncated).
  function answerBlock(item) {
    const wrap = cell("div", null, "reply");
    const preview = item.header.text || "";
    let box = linkified(preview);
    wrap.append(box);
    const long = item.header.truncated || preview.length > 320 || preview.split("\n").length > 5;
    if (!long) {
      box.classList.add("full");
      return wrap;
    }
    const more = cell("button", "показать полностью", "link");
    more.type = "button";
    more.addEventListener("click", async () => {
      more.dataset.off = "1";
      more.disabled = true;
      try {
        if (item.header.truncated) {
          const opened = await fetchSubmission(item);
          const full = linkified(new TextDecoder().decode(opened.file));
          box.replaceWith(full);
          box = full;
        }
        box.classList.add("full");
        more.remove();
      } catch (error) {
        setSubmissionsStatus(`Не удалось открыть ответ: ${error.message}.`, "error");
        more.dataset.off = "";
        more.disabled = false;
      }
    });
    wrap.append(more);
    return wrap;
  }

  // Метка «сдано» у текстового ответа показывает его в списке сдач.
  function revealSubmission(item) {
    $("submissions-work").value = "";
    $("submissions-latest").checked = true;
    renderSubmissions();
    const li = [...$("submissions-list").children].find((row) => row.dataset.id === item.id);
    if (!li) return;
    li.scrollIntoView({ behavior: "smooth", block: "center" });
    li.classList.add("flash");
    setTimeout(() => li.classList.remove("flash"), 1600);
  }

  async function fetchSubmission(item) {
    const response = await fetch(`${config.submitUrl}/api/submissions/${item.id}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`сервер ответил ${response.status}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const opened = await mlgCrypto.openSubmission(await submitPrivateKey(), item.work, bytes);
    if (!opened) throw new Error("файл не расшифровывается");
    if (opened.header.sha256 && (await mlgCrypto.sha256Hex(opened.file)) !== opened.header.sha256) {
      throw new Error("контрольная сумма не сходится");
    }
    return opened;
  }

  // «Фамилия_Имя__2026-10-11_15-03__исходное имя.ipynb»
  function submissionName(item) {
    const clean = (text) => text.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "_").replace(/\s+/g, "_");
    const stamp = new Date(item.received).toLocaleString("sv-SE", { timeZone: "Europe/Moscow" }).slice(0, 16);
    return `${clean(item.header.name)}__${stamp.replace(" ", "_").replace(":", "-")}__${clean(item.header.filename)}`;
  }

  async function downloadSubmission(item) {
    setBusy(true);
    setSubmissionsStatus(`Скачиваю и расшифровываю: ${item.header.name}…`);
    try {
      const opened = await fetchSubmission(item);
      saveFile(new Blob([opened.file], { type: opened.header.type || "application/octet-stream" }), submissionName(item));
      setSubmissionsStatus(`Скачано: ${item.header.name}, ${workTitle(item.work)}.`, "ok");
    } catch (error) {
      setSubmissionsStatus(`Не удалось скачать: ${error.message}.`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function downloadShown() {
    const items = state.shown.filter((item) => item.header);
    if (!items.length) return;
    setBusy(true);
    const files = [];
    const failed = [];
    try {
      for (const [index, item] of items.entries()) {
        setSubmissionsStatus(`Скачиваю ${index + 1} из ${items.length}…`);
        try {
          const opened = await fetchSubmission(item);
          files.push({ name: `${item.work}/${submissionName(item)}`, data: opened.file, date: new Date(item.received) });
        } catch (error) {
          failed.push(`${item.header.name} (${error.message})`);
        }
      }
      if (files.length) {
        const work = $("submissions-work").value;
        saveFile(zipStore(files), `сданные_работы${work ? "_" + work : ""}.zip`);
      }
      setSubmissionsStatus(
        `В архиве файлов: ${files.length}.${failed.length ? ` Не удалось: ${failed.join(", ")}.` : ""}`,
        failed.length ? "error" : "ok",
      );
    } finally {
      setBusy(false);
    }
  }

  // Архив без сжатия (zip «stored»): так код короткий, а ноутбуки и так невелики.
  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function zipStore(files) {
    const parts = [];
    const central = [];
    let offset = 0;
    for (const file of files) {
      const name = encoder.encode(file.name);
      const crc = crc32(file.data);
      const date = file.date;
      const time = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
      const day = ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
      // Общие поля локального и центрального заголовков: версия 2.0, флаг
      // «имена в UTF-8», без сжатия, время, CRC, размеры, длина имени.
      const fields = (view, at) => {
        view.setUint16(at, 20, true);
        view.setUint16(at + 2, 0x0800, true);
        view.setUint16(at + 4, 0, true);
        view.setUint16(at + 6, time, true);
        view.setUint16(at + 8, day, true);
        view.setUint32(at + 10, crc, true);
        view.setUint32(at + 14, file.data.length, true);
        view.setUint32(at + 18, file.data.length, true);
        view.setUint16(at + 22, name.length, true);
      };
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true);
      fields(local, 4);
      parts.push(local.buffer, name, file.data);
      const entry = new DataView(new ArrayBuffer(46));
      entry.setUint32(0, 0x02014b50, true);
      entry.setUint16(4, 20, true);
      fields(entry, 6);
      entry.setUint32(42, offset, true);
      central.push(entry.buffer, name);
      offset += 30 + name.length + file.data.length;
    }
    const size = central.reduce((sum, part) => sum + part.byteLength, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, files.length, true);
    end.setUint16(10, files.length, true);
    end.setUint32(12, size, true);
    end.setUint32(16, offset, true);
    return new Blob([...parts, ...central, end.buffer], { type: "application/zip" });
  }

  function fillSubmissionWorks() {
    const select = $("submissions-work");
    for (const work of config.works) {
      const option = cell("option", work.journal || work.title);
      option.value = work.slug;
      select.append(option);
    }
  }

  // Токен.

  function showTokenState() {
    const local = storageGet("localStorage", KEY_TOKEN);
    const session = storageGet("sessionStorage", KEY_TOKEN);
    $("token-state").textContent = local
      ? "Токен задан и хранится на этом устройстве."
      : session
        ? "Токен задан до закрытия вкладки."
        : "Токен не задан: «Сохранить» будет скачивать файл.";
  }

  function rememberToken() {
    const value = $("token-input").value.trim();
    if (!value) return;
    const persistent = $("token-remember").checked;
    storageSet("localStorage", KEY_TOKEN, persistent ? value : null);
    storageSet("sessionStorage", KEY_TOKEN, persistent ? null : value);
    $("token-input").value = "";
    showTokenState();
  }

  function forgetToken() {
    storageSet("localStorage", KEY_TOKEN, null);
    storageSet("sessionStorage", KEY_TOKEN, null);
    showTokenState();
  }

  // «Выйти» забывает и запомненный вход на этом устройстве.
  async function logout() {
    if (state.dirty && !confirm("Есть несохранённые изменения. Выйти без сохранения?")) return;
    state.dirty = false;
    await forgetSession();
    location.replace(location.pathname);
  }

  // ------------------------------------------------------------ события

  function bind() {
    $("view-form").addEventListener("submit", (event) => {
      event.preventDefault();
      openView($("view-code").value, $("view-remember").checked);
    });
    $("btn-close").addEventListener("click", closeView);

    $("teacher-form").addEventListener("submit", (event) => {
      event.preventDefault();
      teacherLogin($("teacher-password").value);
    });
    $("create-form").addEventListener("submit", (event) => {
      event.preventDefault();
      createJournal($("create-group").value, $("create-password").value, $("create-password2").value);
      $("create-password").value = "";
      $("create-password2").value = "";
    });

    $("btn-save").addEventListener("click", save);
    $("btn-download").addEventListener("click", downloadOnly);
    $("btn-code").addEventListener("click", showCode);
    $("btn-new-code").addEventListener("click", newCode);
    $("btn-logout").addEventListener("click", logout);
    $("btn-copy-code").addEventListener("click", () => copyText(formatCode(state.code), "Код"));
    $("btn-copy-link").addEventListener("click", () => copyText($("code-link").href, "Ссылка"));

    $("btn-preview").addEventListener("click", previewNames);
    $("btn-add").addEventListener("click", addPending);
    $("btn-cancel-add").addEventListener("click", () => {
      state.pending = null;
      $("preview").hidden = true;
    });

    $("group-name").addEventListener("input", () => {
      state.data.group = $("group-name").value.trim();
      markDirty();
    });
    $("btn-add-work").addEventListener("click", () => addWork($("work-name").value));
    $("work-name").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addWork($("work-name").value);
      }
    });

    $("btn-submissions").addEventListener("click", loadSubmissions);
    $("btn-submissions-zip").addEventListener("click", downloadShown);
    $("submissions-work").addEventListener("change", renderSubmissions);
    $("submissions-latest").addEventListener("change", renderSubmissions);

    $("btn-token").addEventListener("click", rememberToken);
    $("btn-token-forget").addEventListener("click", forgetToken);
    $("password-form").addEventListener("submit", (event) => {
      event.preventDefault();
      changePassword($("new-password").value, $("new-password2").value);
    });

    // Ссылку с кодом могут открыть в уже открытой вкладке журнала.
    window.addEventListener("hashchange", () => {
      const fromLink = decodeURIComponent(location.hash.slice(1));
      if (fromLink && state.mode !== "teacher") {
        $("view-code").value = fromLink;
        openView(fromLink, false);
      }
    });

    window.addEventListener("beforeunload", (event) => {
      if (state.dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }

  async function start() {
    if (!window.crypto || !crypto.subtle || !window.mlgCrypto) {
      setStatus("Браузер не поддерживает нужное шифрование. Откройте журнал в свежем Chrome, Firefox, Safari или Edge.", "error");
      return;
    }
    bind();
    fillSubmissionWorks();
    const fromLink = decodeURIComponent(location.hash.slice(1));
    const remembered = storageGet("localStorage", KEY_CODE);
    // Ссылка с кодом — явная просьба открыть журнал группы; иначе сначала
    // запомненный вход преподавателя, потом запомненный код.
    if (fromLink) {
      $("view-code").value = fromLink;
      openView(fromLink, false);
    } else if (await resumeTeacher()) {
      return;
    } else if (remembered) {
      $("view-remember").checked = true;
      openView(remembered, true);
    }
  }

  // Для проверки в консоли и в тестах: разбор списка и нормализация кода.
  window.mlgJournal = { parseNames, canonicalCode, formatCode, generateCode, nameKey, zipStore, columnWorks };

  start();
})();
