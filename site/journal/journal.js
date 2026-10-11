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
 * Сохранение — через GitHub API: файл ложится в ветку `journal`, затем
 * запускается пересборка сайта. Токен вводит преподаватель, в журнал он
 * не попадает.
 */
(function () {
  "use strict";

  const OWNER = "nikita-konkin";
  const REPO = "basic-linguistic-ml";
  const BRANCH = "journal";
  const FILE = "journal.json";
  const WORKFLOW = "site.yml";
  const API = `https://api.github.com/repos/${OWNER}/${REPO}`;

  const FORMAT = 1;
  const ITERATIONS = 600000;
  const MIN_PASSWORD = 12;
  const MAX_SCORE = 10;
  // На случай, если страница открыта без сборки и списка работ курса в ней нет.
  const FALLBACK_WORKS = ["Л.р. № 1", "Л.р. № 2", "Л.р. № 3"];

  // Без 0/O, 1/I/L: код диктуют и переписывают с доски.
  const ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
  const CODE_LENGTH = 12;
  // Кириллица, которую легко набрать вместо латиницы.
  const LOOKALIKE = { А: "A", В: "B", Е: "E", К: "K", М: "M", Н: "H", О: "O", Р: "P", С: "C", Т: "T", У: "Y", Х: "X" };

  const KEY_CODE = "mlg-journal-code";
  const KEY_TOKEN = "mlg-journal-token";

  const $ = (id) => document.getElementById(id);
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  const state = {
    mode: null, // "view" | "teacher"
    data: null, // {group, works, students: [{name, scores}]}
    code: null, // код группы, только у преподавателя
    password: null,
    sha: null, // версия файла в ветке journal, если известна
    loadedText: null, // текст файла, с которого начата правка
    dirty: false,
    invalid: new Set(),
    recent: new Set(),
    pending: null, // ФИО из «Проверить список»
  };

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

  // ------------------------------------------------------------- шифр

  function toBase64(bytes) {
    let text = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(text);
  }

  function fromBase64(text) {
    const raw = atob(text.replace(/\s+/g, ""));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  function random(length) {
    return crypto.getRandomValues(new Uint8Array(length));
  }

  async function deriveKey(secret, salt, iterations) {
    const base = await crypto.subtle.importKey("raw", encoder.encode(secret), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
      base,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  }

  // Имя ящика входит в проверяемые данные: подменить view на teacher нельзя.
  function label(box) {
    return encoder.encode(`mlg-journal/${FORMAT}/${box}`);
  }

  async function seal(payload, secret, box) {
    const salt = random(16);
    const iv = random(12);
    const key = await deriveKey(secret, salt, ITERATIONS);
    const data = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv, additionalData: label(box) },
      key,
      encoder.encode(JSON.stringify(payload)),
    );
    return { salt: toBase64(salt), iv: toBase64(iv), data: toBase64(new Uint8Array(data)) };
  }

  async function open(file, secret, box) {
    const sealed = file[box];
    const key = await deriveKey(secret, fromBase64(sealed.salt), file.kdf.iterations);
    let plain;
    try {
      plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: fromBase64(sealed.iv), additionalData: label(box) },
        key,
        fromBase64(sealed.data),
      );
    } catch (error) {
      return null; // неверный код или пароль
    }
    return JSON.parse(decoder.decode(plain));
  }

  // ------------------------------------------------------- код группы

  function generateCode() {
    let out = "";
    while (out.length < CODE_LENGTH) {
      for (const byte of random(32)) {
        // 248 = 31·8: отбрасываем хвост, чтобы все знаки были равновероятны.
        if (byte < 248 && out.length < CODE_LENGTH) out += ALPHABET[byte % ALPHABET.length];
      }
    }
    return out;
  }

  function canonicalCode(input) {
    let text = (input || "").toUpperCase();
    text = text.replace(/[А-ЯЁ]/g, (ch) => LOOKALIKE[ch] || ch);
    return text.replace(/[^0-9A-Z]/g, "");
  }

  function formatCode(code) {
    return code.match(/.{1,4}/g).join("-");
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

  function setBusy(busy) {
    document.querySelectorAll("button").forEach((button) => {
      button.disabled = busy;
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
    data.works.forEach((work) => headRow.append(cell("th", work, "num work")));
    headRow.append(cell("th", "сумма", "num"));
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

      const sumCell = cell("td", null, "num");
      const showSum = () => {
        const value = total(student.scores);
        sumCell.textContent = value === null ? "—" : String(value);
      };

      data.works.forEach((work, column) => {
        const value = student.scores[column];
        const td = cell("td", null, "num");
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
            } else {
              state.invalid.add(id);
            }
            markDirty();
          });
          td.append(input);
        } else {
          td.textContent = typeof value === "number" ? String(value) : "—";
        }
        tr.append(td);
      });
      showSum();
      tr.append(sumCell);
      body.append(tr);
    });

    table.append(head, body);
    const count = data.students.length;
    $("table-note").textContent = count
      ? `Студентов: ${count}. «—» — работа ещё не оценена. Баллы — от 0 до ${MAX_SCORE}.`
      : teacher
        ? "Студентов пока нет — добавьте список ниже."
        : "Студентов пока нет.";
    $("subtitle").textContent = data.group
      ? `Группа ${data.group}. Баллы за лабораторные работы, от 0 до ${MAX_SCORE}.`
      : `Баллы за лабораторные работы, от 0 до ${MAX_SCORE}.`;
    $("table-section").hidden = false;
    if (teacher) renderWorks();
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
    data.students.forEach((student) => {
      student.scores = data.works.map((_, index) => {
        const value = student.scores ? student.scores[index] : null;
        return typeof value === "number" ? value : null;
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
      const payload = await open(copy.file, password.normalize("NFC"), "teacher");
      if (!payload) {
        setStatus("Неверный пароль преподавателя.", "error");
        return;
      }
      const code = payload.code;
      delete payload.code;
      startTeacher(checkData(payload), code, password.normalize("NFC"), copy);
      setStatus("Журнал открыт. Изменения сохраняются кнопкой «Сохранить».", "ok");
    } catch (error) {
      setStatus(`Не удалось открыть журнал: ${error.message}.`, "error");
    } finally {
      setBusy(false);
    }
  }

  function startTeacher(data, code, password, copy) {
    state.mode = "teacher";
    state.data = data;
    state.code = code;
    state.password = password;
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
    showTokenState();
    renderTable();
  }

  function createJournal(group, password, again) {
    if (password.length < MIN_PASSWORD) {
      setStatus(`Пароль слишком короткий: нужно не меньше ${MIN_PASSWORD} знаков.`, "error");
      return;
    }
    if (password !== again) {
      setStatus("Пароли не совпадают.", "error");
      return;
    }
    const data = { group: group.trim(), works: courseWorks(), students: [] };
    startTeacher(data, generateCode(), password.normalize("NFC"), null);
    showCode();
    setStatus("Журнал создан. Добавьте студентов и нажмите «Сохранить» — до этого он нигде не хранится.", "ok");
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

  function changePassword(password, again) {
    if (password.length < MIN_PASSWORD) {
      setStatus(`Пароль слишком короткий: нужно не меньше ${MIN_PASSWORD} знаков.`, "error");
      return;
    }
    if (password !== again) {
      setStatus("Пароли не совпадают.", "error");
      return;
    }
    state.password = password.normalize("NFC");
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
      state.data.students.push({ name, scores: state.data.works.map(() => null) });
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
    state.data.students.forEach((student) => student.scores.push(null));
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
    state.data.students.forEach((student) => swap(student.scores));
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
    state.data.students.forEach((student) => student.scores.splice(index, 1));
    markDirty();
    renderTable();
  }

  // Сохранение.

  async function buildFile() {
    const data = { group: state.data.group, works: state.data.works, students: state.data.students };
    const file = {
      format: FORMAT,
      kdf: { name: "PBKDF2", hash: "SHA-256", iterations: ITERATIONS },
      view: await seal(data, state.code, "view"),
      teacher: await seal({ ...data, code: state.code }, state.password, "teacher"),
    };
    return JSON.stringify(file, null, 1) + "\n";
  }

  function download(text) {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = FILE;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
        setStatus(
          "Токен не задан, поэтому файл скачан. Положите его в ветку journal репозитория " +
            "(GitHub → ветка journal → Add file → Upload files), затем запустите пересборку: " +
            "Actions → «Сайт курса» → Run workflow.",
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
      state.recent.clear();
      renderTable();

      const dispatch = await github(`/actions/workflows/${WORKFLOW}/dispatches`, {
        method: "POST",
        body: JSON.stringify({ ref: "main" }),
      });
      if (dispatch.ok) {
        setStatus("Сохранено. Группа увидит изменения на сайте через 1–2 минуты.", "ok");
      } else {
        setStatus(
          "Журнал сохранён, но сайт не пересобрался: у токена, видимо, нет права Actions — Read and write. " +
            "Пересоберите вручную: Actions → «Сайт курса» → Run workflow.",
          "error",
        );
      }
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

  function logout() {
    if (state.dirty && !confirm("Есть несохранённые изменения. Выйти без сохранения?")) return;
    state.dirty = false;
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

  function start() {
    if (!window.crypto || !crypto.subtle) {
      setStatus("Браузер не поддерживает нужное шифрование. Откройте журнал в свежем Chrome, Firefox, Safari или Edge.", "error");
      return;
    }
    bind();
    const fromLink = decodeURIComponent(location.hash.slice(1));
    const remembered = storageGet("localStorage", KEY_CODE);
    if (fromLink) {
      $("view-code").value = fromLink;
      openView(fromLink, false);
    } else if (remembered) {
      $("view-remember").checked = true;
      openView(remembered, true);
    }
  }

  // Для проверки в консоли и в тестах: разбор списка и нормализация кода.
  window.mlgJournal = { parseNames, canonicalCode, formatCode, generateCode, nameKey };

  start();
})();
