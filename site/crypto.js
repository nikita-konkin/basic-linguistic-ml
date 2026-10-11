/* Шифрование сайта курса: журнал группы и сдача работ. Только WebCrypto.
 *
 * Журнал (journal.json) — два «ящика» AES-256-GCM с одними данными, ключ
 * из кода группы или пароля преподавателя через PBKDF2-SHA256. Подробнее —
 * в journal/journal.js.
 *
 * Сдача работ — конверт на открытый ключ преподавателя. Ключевая пара
 * ECDH P-256 лежит в журнале: открытая половина — в ящике группы (её видит
 * страница сдачи), закрытая — только в ящике преподавателя. Для каждой
 * сдачи браузер студента делает одноразовую пару, общий секрет ECDH
 * превращается в ключ AES-256-GCM через HKDF-SHA256. Разметка конверта
 * описана в submit-server/server.py; сервер приёма его не расшифровывает.
 */
(function () {
  "use strict";

  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  // --------------------------------------------------------------- байты

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

  function concat(parts) {
    const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  }

  async function sha256Hex(bytes) {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  // ------------------------------------------------------ ящики журнала

  const JOURNAL_FORMAT = 1;
  const ITERATIONS = 600000;

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
  function boxLabel(box) {
    return encoder.encode(`mlg-journal/${JOURNAL_FORMAT}/${box}`);
  }

  async function sealBox(payload, secret, box) {
    const salt = random(16);
    const iv = random(12);
    const key = await deriveKey(secret, salt, ITERATIONS);
    const data = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv, additionalData: boxLabel(box) },
      key,
      encoder.encode(JSON.stringify(payload)),
    );
    return { salt: toBase64(salt), iv: toBase64(iv), data: toBase64(new Uint8Array(data)) };
  }

  // null — неверный код или пароль.
  async function openBox(file, secret, box) {
    const sealed = file[box];
    const key = await deriveKey(secret, fromBase64(sealed.salt), file.kdf.iterations);
    let plain;
    try {
      plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: fromBase64(sealed.iv), additionalData: boxLabel(box) },
        key,
        fromBase64(sealed.data),
      );
    } catch (error) {
      return null;
    }
    return JSON.parse(decoder.decode(plain));
  }

  // --------------------------------------------------------- код группы

  // Без 0/O, 1/I/L: код диктуют и переписывают с доски.
  const ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
  const CODE_LENGTH = 12;
  // Кириллица, которую легко набрать вместо латиницы.
  const LOOKALIKE = { А: "A", В: "B", Е: "E", К: "K", М: "M", Н: "H", О: "O", Р: "P", С: "C", Т: "T", У: "Y", Х: "X" };

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

  // ------------------------------------------------------- сдача работ

  const MAGIC = encoder.encode("MLGS1");
  const POINT = 65;
  const IV = 12;
  const SUBMIT_INFO = encoder.encode("mlg-submit/1");
  const CURVE = { name: "ECDH", namedCurve: "P-256" };

  function submitLabel(work, part) {
    return encoder.encode(`mlg-submit/1/${work}/${part}`);
  }

  // Пара ключей преподавателя, в виде JWK: так она хранится в журнале.
  async function generateSubmitKeys() {
    const pair = await crypto.subtle.generateKey(CURVE, true, ["deriveBits"]);
    const { kty, crv, x, y, d } = await crypto.subtle.exportKey("jwk", pair.privateKey);
    return { publicKey: { kty, crv, x, y }, privateKey: { kty, crv, x, y, d } };
  }

  function importPublic(jwk) {
    return crypto.subtle.importKey("jwk", jwk, CURVE, false, []);
  }

  function importPrivate(jwk) {
    return crypto.subtle.importKey("jwk", jwk, CURVE, false, ["deriveBits"]);
  }

  // Ключ AES из общего секрета ECDH; соль — одноразовый открытый ключ.
  async function envelopeKey(privateKey, publicKey, ephemeralRaw) {
    const shared = await crypto.subtle.deriveBits({ name: "ECDH", public: publicKey }, privateKey, 256);
    const base = await crypto.subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: ephemeralRaw, info: SUBMIT_INFO },
      base,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"],
    );
  }

  function uint32(value) {
    const bytes = new Uint8Array(4);
    new DataView(bytes.buffer).setUint32(0, value);
    return bytes;
  }

  // Заголовок (ФИО, работа, имя файла…) и файл шифруются отдельно, чтобы
  // журнал показывал список сдач, не скачивая сами работы.
  async function sealSubmission(publicJwk, work, header, fileBytes) {
    const teacher = await importPublic(publicJwk);
    const ephemeral = await crypto.subtle.generateKey(CURVE, true, ["deriveBits"]);
    const ephemeralRaw = new Uint8Array(await crypto.subtle.exportKey("raw", ephemeral.publicKey));
    const key = await envelopeKey(ephemeral.privateKey, teacher, ephemeralRaw);
    const headIv = random(IV);
    const bodyIv = random(IV);
    const head = new Uint8Array(
      await crypto.subtle.encrypt(
        { name: "AES-GCM", iv: headIv, additionalData: submitLabel(work, "head") },
        key,
        encoder.encode(JSON.stringify(header)),
      ),
    );
    const body = new Uint8Array(
      await crypto.subtle.encrypt({ name: "AES-GCM", iv: bodyIv, additionalData: submitLabel(work, "body") }, key, fileBytes),
    );
    return concat([MAGIC, ephemeralRaw, uint32(head.length), headIv, head, bodyIv, body]);
  }

  // Разбор конверта или его начала (до конца заголовка — так его отдаёт список).
  function parseEnvelope(bytes) {
    const magic = bytes.subarray(0, MAGIC.length);
    if (bytes.length < MAGIC.length + POINT + 4 + IV || !magic.every((byte, i) => byte === MAGIC[i])) return null;
    let offset = MAGIC.length;
    const ephemeralRaw = bytes.slice(offset, offset + POINT);
    offset += POINT;
    const headLength = new DataView(bytes.buffer, bytes.byteOffset + offset, 4).getUint32(0);
    offset += 4;
    const headIv = bytes.slice(offset, offset + IV);
    offset += IV;
    if (bytes.length < offset + headLength) return null;
    const head = bytes.slice(offset, offset + headLength);
    offset += headLength;
    const rest = bytes.length >= offset + IV ? { bodyIv: bytes.slice(offset, offset + IV), body: bytes.subarray(offset + IV) } : {};
    return { ephemeralRaw, headIv, head, ...rest };
  }

  async function openParts(privateKey, work, bytes, withBody) {
    const parts = parseEnvelope(bytes);
    if (!parts || (withBody && !parts.body)) return null;
    try {
      const ephemeral = await crypto.subtle.importKey("raw", parts.ephemeralRaw, CURVE, false, []);
      const key = await envelopeKey(privateKey, ephemeral, parts.ephemeralRaw);
      const head = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: parts.headIv, additionalData: submitLabel(work, "head") },
        key,
        parts.head,
      );
      const header = JSON.parse(decoder.decode(head));
      if (!withBody) return { header };
      const file = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: parts.bodyIv, additionalData: submitLabel(work, "body") },
        key,
        parts.body,
      );
      return { header, file: new Uint8Array(file) };
    } catch (error) {
      return null; // чужой ключ, другая работа или испорченный конверт
    }
  }

  window.mlgCrypto = {
    toBase64,
    fromBase64,
    sha256Hex,
    ITERATIONS,
    JOURNAL_FORMAT,
    sealBox,
    openBox,
    CODE_LENGTH,
    generateCode,
    canonicalCode,
    formatCode,
    generateSubmitKeys,
    importPrivate,
    sealSubmission,
    // privateKey — результат importPrivate; null, если конверт не открывается.
    openSubmissionHead: (privateKey, work, bytes) => openParts(privateKey, work, bytes, false),
    openSubmission: (privateKey, work, bytes) => openParts(privateKey, work, bytes, true),
  };
})();
