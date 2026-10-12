/* Сдача работы: код группы → ФИО → работа → файл или текст → квитанция.
 *
 * По коду группы страница открывает журнал (тот же ящик view, что видит
 * группа), берёт из него список ФИО и открытый ключ преподавателя. Файл
 * шифруется этим ключом здесь же, в браузере (../crypto.js), и уходит на
 * сервер приёма уже шифротекстом. Расшифровать его может только журнал
 * преподавателя.
 *
 * Ноутбук до отправки проверяется: запущены ли ячейки, нет ли ошибок в
 * выводе, совпадает ли заголовок с выбранной работой. Это подсказки
 * студенту; отправке они не мешают, кроме явно битого файла.
 *
 * Вместо файла можно отправить текст — обычно ссылку на Colab или
 * репозиторий. Он уходит тем же конвертом (как «ответ.txt»), а его начало
 * кладётся и в заголовок, чтобы журнал показывал ответ прямо в списке.
 */
(function () {
  "use strict";

  const MAX_FILE = 10 * 1024 * 1024;
  const TYPES = {
    ipynb: "application/x-ipynb+json",
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    zip: "application/zip",
  };
  const KEY_CODE = "mlg-journal-code"; // общий с журналом
  const KEY_NAME = "mlg-submit-name";

  const MAX_TEXT = 20000;
  // Столько знаков текстового ответа идёт и в заголовок конверта: журнал
  // показывает его в списке сдач, не скачивая ответы. Заголовок на сервере
  // ограничен 8 КБ, поэтому с запасом.
  const PREVIEW = 1500;
  const MAX_HEADER = 6000;
  const URL_PATTERN = /https?:\/\/[^\s<>"«»]+[^\s<>"«».,;:!?)]/g;
  // Где доступ по ссылке по умолчанию закрыт.
  const CLOSED_BY_DEFAULT = /(^|\.)((colab\.research|drive|docs)\.google\.com|disk\.yandex\.(ru|com)|onedrive\.live\.com|1drv\.ms)$/;

  const $ = (id) => document.getElementById(id);
  const { canonicalCode, formatCode, CODE_LENGTH } = mlgCrypto;

  const config = readConfig();
  const state = {
    names: [],
    submitKey: null, // открытый ключ преподавателя (JWK)
    file: null, // {name, bytes, type, check}
    fileProblems: [], // проверки файла: [{level, text}]
    problems: [], // проверки того, что сейчас будет отправлено
  };

  function readConfig() {
    const element = document.getElementById("mlg-config");
    try {
      const value = element ? JSON.parse(element.textContent) : {};
      return { submitUrl: (value.submitUrl || "").replace(/\/+$/, ""), works: value.works || [] };
    } catch (error) {
      return { submitUrl: "", works: [] };
    }
  }

  function storageGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (error) {
      /* хранилище недоступно: просто не запоминаем */
    }
  }

  function setStatus(text, kind) {
    const box = $("status");
    box.textContent = text || "";
    box.className = "status" + (kind ? " " + kind : "");
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function sizeText(bytes) {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} МБ`;
  }

  function work(slug) {
    return config.works.find((item) => item.slug === slug) || null;
  }

  // ------------------------------------------------------- код группы

  async function openGroup(input, remember) {
    const code = canonicalCode(input);
    if (code.length !== CODE_LENGTH) {
      setStatus(`В коде группы ${CODE_LENGTH} знаков, например ABCD-EFGH-JKMN. Проверьте, всё ли набрано.`, "error");
      return;
    }
    setStatus("Открываю список группы…");
    try {
      const response = await fetch("../journal/journal.json", { cache: "no-store" });
      if (response.status === 404) {
        setStatus("Журнал группы ещё не опубликован — сдавать пока некуда.", "error");
        return;
      }
      if (!response.ok) throw new Error(`сайт ответил ${response.status}`);
      const file = await response.json();
      const data = await mlgCrypto.openBox(file, code, "view");
      if (!data) {
        setStatus("Код не подходит. Проверьте его у преподавателя.", "error");
        return;
      }
      if (!data.submitKey) {
        setStatus(
          "Преподаватель ещё не включил приём работ в журнале. Попробуйте позже или сдайте работу так, как договорились на занятии.",
          "error",
        );
        return;
      }
      if (remember) storageSet(KEY_CODE, code);
      state.names = data.students.map((student) => student.name).sort((a, b) => a.localeCompare(b, "ru"));
      state.submitKey = data.submitKey;
      showForm(data.group);
      setStatus("");
    } catch (error) {
      setStatus(`Не удалось открыть список группы: ${error.message}.`, "error");
    }
  }

  function showForm(group) {
    const select = $("student");
    select.length = 1;
    for (const name of state.names) select.append(new Option(name, name));
    const remembered = storageGet(KEY_NAME);
    if (remembered && state.names.includes(remembered)) select.value = remembered;
    $("group-label").textContent = group ? `Группа ${group}: в списке ${state.names.length} чел.` : `В списке группы ${state.names.length} чел.`;
    $("step-code").hidden = true;
    $("step-group").hidden = false;
    $("submit-form").hidden = false;
    $("receipt").hidden = true;
  }

  function otherCode() {
    storageSet(KEY_CODE, null);
    state.submitKey = null;
    $("code").value = "";
    $("step-code").hidden = false;
    $("step-group").hidden = true;
    $("submit-form").hidden = true;
    $("receipt").hidden = true;
    $("code").focus();
  }

  // ---------------------------------------------------- проверка файла

  function headingKey(text) {
    return (text || "").normalize("NFC").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
  }

  function cellText(source) {
    return Array.isArray(source) ? source.join("") : source || "";
  }

  // Ноутбук: сколько ячеек с кодом запущено, где ошибки, чей заголовок.
  function checkNotebook(text, slug) {
    let notebook;
    try {
      notebook = JSON.parse(text);
    } catch (error) {
      return {
        problems: [{ level: "error", text: "Файл не читается как ноутбук Jupyter. Откройте его в Jupyter и сохраните заново." }],
        check: null,
      };
    }
    if (!notebook || !Array.isArray(notebook.cells) || notebook.nbformat !== 4) {
      return {
        problems: [{ level: "error", text: "Это не ноутбук Jupyter (нет ячеек или незнакомый формат)." }],
        check: null,
      };
    }
    const problems = [];
    const code = notebook.cells.filter((cell) => cell.cell_type === "code" && cellText(cell.source).trim());
    const executed = code.filter((cell) => typeof cell.execution_count === "number");
    const failed = code.filter((cell) => (cell.outputs || []).some((output) => output.output_type === "error"));

    if (!code.length) {
      problems.push({ level: "warn", text: "В ноутбуке нет ни одной ячейки с кодом." });
    } else if (!executed.length) {
      problems.push({
        level: "warn",
        text: "Ноутбук не запускался: ни одна ячейка не выполнена. Выполните все ячейки (Run All) и сохраните файл.",
      });
    } else if (executed.length < code.length) {
      problems.push({
        level: "warn",
        text: `Выполнено ячеек с кодом: ${executed.length} из ${code.length}. Лучше перезапустить всё (Restart & Run All) и сохранить.`,
      });
    } else {
      problems.push({ level: "ok", text: `Все ячейки с кодом выполнены (${code.length}).` });
    }

    if (failed.length) {
      const names = [...new Set(failed.flatMap((cell) => cell.outputs.filter((o) => o.output_type === "error").map((o) => o.ename)))];
      problems.push({
        level: "warn",
        text: `В выводе ошибки: ячеек — ${failed.length} (${names.filter(Boolean).slice(0, 3).join(", ") || "ошибка"}).`,
      });
    } else if (executed.length) {
      problems.push({ level: "ok", text: "Ошибок в выводе нет." });
    }

    // Заголовок первой ячейки «# …» сверяется с ноутбуками курса.
    let heading = "";
    for (const cell of notebook.cells) {
      if (cell.cell_type !== "markdown") continue;
      const match = cellText(cell.source).match(/^#\s+(.+)$/m);
      if (match) {
        heading = headingKey(match[1]);
        break;
      }
    }
    const owner = config.works.find((item) => item.heading && headingKey(item.heading) === heading);
    let headingState = "unknown";
    if (owner && owner.slug === slug) {
      headingState = "match";
      problems.push({ level: "ok", text: `Это ноутбук работы «${owner.journal || owner.title}».` });
    } else if (owner) {
      headingState = "other";
      problems.push({
        level: "warn",
        text: `Похоже, это ноутбук другой работы — «${owner.journal || owner.title}». Проверьте файл и выбранную работу.`,
      });
    } else {
      problems.push({ level: "warn", text: "Заголовок ноутбука не совпадает ни с одной работой курса — тот ли это файл?" });
    }

    return {
      problems,
      check: {
        cells: code.length,
        executed: executed.length,
        errors: failed.length,
        heading: headingState,
        headingWork: owner ? owner.slug : null,
      },
    };
  }

  function extension(name) {
    const match = /\.([a-z0-9]+)$/i.exec(name);
    return match ? match[1].toLowerCase() : "";
  }

  async function chooseFile(file) {
    state.file = null;
    state.fileProblems = [];
    if (!file) {
      refreshProblems();
      return;
    }
    const kind = extension(file.name);
    const problems = [];
    if (!TYPES[kind]) {
      problems.push({ level: "error", text: "Принимаются файлы .ipynb, .pdf, .docx и .zip." });
    } else if (file.size > MAX_FILE) {
      problems.push({ level: "error", text: `Файл больше 10 МБ (${sizeText(file.size)}). Очистите вывод тяжёлых ячеек или сожмите в .zip.` });
    } else if (!file.size) {
      problems.push({ level: "error", text: "Файл пустой." });
    }
    let bytes = null;
    let check = null;
    if (!problems.length) {
      bytes = new Uint8Array(await file.arrayBuffer());
      if (kind === "ipynb") {
        const result = checkNotebook(new TextDecoder().decode(bytes), $("work").value);
        problems.push(...result.problems);
        check = result.check;
      }
    }
    state.file = { name: file.name, size: file.size, type: TYPES[kind] || "", bytes, check, kind };
    state.fileProblems = problems;
    refreshProblems();
  }

  // Проверка ноутбука зависит от выбранной работы: пересчитать при смене.
  function recheck() {
    const file = state.file;
    if (!file || file.kind !== "ipynb" || !file.bytes) return;
    const result = checkNotebook(new TextDecoder().decode(file.bytes), $("work").value);
    file.check = result.check;
    state.fileProblems = result.problems;
    refreshProblems();
  }

  // ------------------------------------------------- текстовый ответ

  function mode() {
    return document.querySelector('input[name="mode"]:checked').value;
  }

  function checkAnswer(text) {
    const answer = text.trim();
    if (!answer) return [];
    if (answer.length > MAX_TEXT) {
      return [{ level: "error", text: `Текст длиннее ${MAX_TEXT} знаков — пришлите его файлом.` }];
    }
    const links = answer.match(URL_PATTERN) || [];
    if (!links.length) return [{ level: "ok", text: `Текстовый ответ, знаков: ${answer.length}.` }];
    const hosts = [
      ...new Set(
        links
          .map((link) => {
            try {
              return new URL(link).hostname;
            } catch (error) {
              return "";
            }
          })
          .filter(Boolean),
      ),
    ];
    const problems = [{ level: "ok", text: `Ссылок: ${links.length} (${hosts.join(", ")}).` }];
    if (hosts.some((host) => CLOSED_BY_DEFAULT.test(host))) {
      problems.push({
        level: "info",
        text: "Здесь доступ по ссылке по умолчанию закрыт. Проверьте, что открыт доступ «Все, у кого есть ссылка», иначе преподаватель работу не увидит.",
      });
    }
    return problems;
  }

  function refreshProblems() {
    const text = mode() === "text";
    $("mode-file").hidden = text;
    $("mode-text").hidden = !text;
    state.problems = text ? checkAnswer($("answer").value) : state.fileProblems;
    renderChecks();
  }

  function renderChecks() {
    const list = $("checks");
    list.replaceChildren();
    const file = state.file;
    $("drop-main").textContent = file ? `${file.name} · ${sizeText(file.size)}` : "Перетащите файл сюда или нажмите, чтобы выбрать";
    $("drop").classList.toggle("chosen", Boolean(file));
    for (const problem of state.problems) {
      const mark = { ok: "✓", info: "i", warn: "!", error: "×" }[problem.level];
      const li = element("li", null, problem.level);
      li.append(element("span", mark, "mark"), element("span", problem.text));
      list.append(li);
    }
    list.hidden = !state.problems.length;
  }

  // ------------------------------------------------------------ отправка

  // Что уходит в конверт: файл или текст, в одном виде.
  function answer() {
    if (mode() === "text") {
      const text = $("answer").value.trim();
      if (!text) return null;
      return {
        kind: "text",
        bytes: new TextEncoder().encode(text),
        filename: "ответ.txt",
        type: "text/plain; charset=utf-8",
        check: null,
        text,
      };
    }
    const file = state.file;
    if (!file) return null;
    return { kind: "file", bytes: file.bytes, filename: file.name.normalize("NFC").slice(-200), type: file.type, check: file.check };
  }

  async function send() {
    const name = $("student").value;
    const slug = $("work").value;
    const reply = answer();
    if (!name) return setStatus("Выберите себя в списке группы.", "error");
    if (!slug) return setStatus("Выберите работу.", "error");
    if (!reply) {
      return setStatus(mode() === "text" ? "Напишите ответ или вставьте ссылку." : "Выберите файл с работой.", "error");
    }
    if (state.problems.some((problem) => problem.level === "error")) {
      return setStatus("Это отправить нельзя — см. сообщения ниже.", "error");
    }
    const warnings = state.problems.filter((problem) => problem.level === "warn").length;
    if (warnings && !confirm(`Есть замечания (${warnings}). Всё равно отправить?`)) return;

    storageSet(KEY_NAME, name);
    const button = $("btn-send");
    button.disabled = true;
    setStatus("Шифрую…");
    try {
      const sha256 = await mlgCrypto.sha256Hex(reply.bytes);
      const header = {
        v: 1,
        name,
        work: slug,
        kind: reply.kind,
        filename: reply.filename,
        type: reply.type,
        size: reply.bytes.length,
        sha256,
        sent: new Date().toISOString(),
        check: reply.check,
      };
      if (reply.kind === "text") {
        let preview = reply.text.slice(0, PREVIEW);
        header.text = preview;
        while (new TextEncoder().encode(JSON.stringify(header)).length > MAX_HEADER) {
          preview = preview.slice(0, Math.floor(preview.length * 0.8));
          header.text = preview;
        }
        header.truncated = preview.length < reply.text.length;
      }
      const envelope = await mlgCrypto.sealSubmission(state.submitKey, slug, header, reply.bytes);
      setStatus("Отправляю…");
      let response;
      try {
        response = await fetch(`${config.submitUrl}/api/submissions?work=${encodeURIComponent(slug)}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: envelope,
        });
      } catch (error) {
        throw new Error("сервер приёма не отвечает. Проверьте интернет и попробуйте ещё раз через несколько минут");
      }
      const receipt = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(receipt.error || `сервер ответил ${response.status}`);
      showReceipt(header, receipt);
      setStatus("");
    } catch (error) {
      setStatus(`Работа не отправлена: ${error.message}.`, "error");
    } finally {
      button.disabled = false;
    }
  }

  function showReceipt(header, receipt) {
    const item = work(header.work);
    $("r-name").textContent = header.name;
    $("r-work").textContent = item ? item.title : header.work;
    const text = header.kind === "text";
    $("r-file-label").textContent = text ? "Ответ" : "Файл";
    $("r-file").textContent = text ? `текст, знаков: ${$("answer").value.trim().length}` : `${header.filename} · ${sizeText(header.size)}`;
    $("r-time").textContent = new Date(receipt.received).toLocaleString("ru-RU", {
      timeZone: "Europe/Moscow",
      day: "numeric",
      month: "long",
      hour: "2-digit",
      minute: "2-digit",
    });
    $("r-id").textContent = receipt.id;
    $("r-sum").textContent = header.sha256.slice(0, 16).match(/.{4}/g).join(" ");
    $("submit-form").hidden = true;
    $("receipt").hidden = false;
    $("receipt").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function again() {
    $("file").value = "";
    $("answer").value = "";
    chooseFile(null);
    $("receipt").hidden = true;
    $("submit-form").hidden = false;
  }

  // ------------------------------------------------------------ события

  function bind() {
    $("code-form").addEventListener("submit", (event) => {
      event.preventDefault();
      openGroup($("code").value, $("remember").checked);
    });
    $("btn-other-code").addEventListener("click", otherCode);
    $("submit-form").addEventListener("submit", (event) => {
      event.preventDefault();
      send();
    });
    $("btn-again").addEventListener("click", again);
    $("work").addEventListener("change", () => {
      recheck();
      const slug = $("work").value;
      history.replaceState(null, "", slug ? `?work=${encodeURIComponent(slug)}` : location.pathname);
    });
    $("file").addEventListener("change", () => chooseFile($("file").files[0]));
    document.querySelectorAll('input[name="mode"]').forEach((radio) => radio.addEventListener("change", refreshProblems));
    $("answer").addEventListener("input", refreshProblems);

    const drop = $("drop");
    ["dragenter", "dragover"].forEach((type) =>
      drop.addEventListener(type, (event) => {
        event.preventDefault();
        drop.classList.add("over");
      }),
    );
    ["dragleave", "drop"].forEach((type) =>
      drop.addEventListener(type, () => {
        drop.classList.remove("over");
      }),
    );
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      const file = event.dataTransfer.files[0];
      if (file) chooseFile(file);
    });
  }

  function start() {
    if (!window.crypto || !crypto.subtle) {
      setStatus("Браузер не поддерживает нужное шифрование. Откройте страницу в свежем Chrome, Firefox, Safari или Edge.", "error");
      return;
    }
    if (!config.submitUrl) {
      setStatus("Приём работ через сайт сейчас выключен. Сдайте работу так, как договорились на занятии.", "error");
      $("step-code").hidden = true;
      return;
    }
    const select = $("work");
    for (const item of config.works) select.append(new Option(item.title, item.slug));
    const wanted = new URLSearchParams(location.search).get("work");
    if (wanted && work(wanted)) select.value = wanted;
    bind();

    const fromLink = decodeURIComponent(location.hash.slice(1));
    const remembered = storageGet(KEY_CODE);
    if (fromLink) {
      $("code").value = fromLink;
      openGroup(fromLink, false);
    } else if (remembered) {
      $("code").value = formatCode(remembered);
      $("remember").checked = true;
      openGroup(remembered, true);
    }
  }

  // Для проверки в консоли: разбор ноутбука.
  window.mlgSubmit = { checkNotebook, checkAnswer };

  start();
})();
