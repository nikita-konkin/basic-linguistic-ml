/* Кнопка смены темы: светлая или тёмная.
 *
 * Пока посетитель ничего не выбрал, тема следует настройке системы (это
 * делает CSS). Выбор кладётся в атрибут data-theme у <html> и запоминается
 * на этом устройстве; до отрисовки его восстанавливает строчка в <head>.
 * Без JavaScript кнопка не показывается.
 */
(function () {
  "use strict";

  var KEY = "mlg-theme";
  var root = document.documentElement;
  var button = document.querySelector(".theme-toggle");
  if (!button) return;
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function current() {
    if (root.dataset.theme === "light" || root.dataset.theme === "dark") return root.dataset.theme;
    return media && media.matches ? "dark" : "light";
  }

  // Надпись говорит, что будет после нажатия.
  function label() {
    var toDark = current() === "light";
    button.textContent = toDark ? "тёмная тема" : "светлая тема";
    button.setAttribute("aria-label", toDark ? "Включить тёмную тему" : "Включить светлую тему");
  }

  button.addEventListener("click", function () {
    var next = current() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch (error) {
      /* хранилище недоступно: тема сменится до перезагрузки */
    }
    label();
  });

  if (media && media.addEventListener) media.addEventListener("change", label);
  label();
  button.hidden = false;
})();
