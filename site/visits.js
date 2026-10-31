/* Счётчик посещений GoatCounter: один запрос картинки на страницу, без cookies
 * и без чужого кода. Уходит только путь страницы — ни запрос, ни фрагмент
 * адреса. Локальный просмотр и браузер под управлением автоматизации не
 * считаются.
 */
(function () {
  "use strict";

  if (location.protocol === "file:" || navigator.webdriver) return;
  if (/^(localhost|127\.|\[::1\])/.test(location.hostname)) return;
  new Image().src = "https://konkin-nikita.goatcounter.com/count?p=" + encodeURIComponent(location.pathname) +
    "&t=" + encodeURIComponent(document.title) + "&r=" + encodeURIComponent(document.referrer) +
    "&rnd=" + Math.random().toString(36).slice(2);
})();
