/* Station version/period menu: switches the body's data-active-variant
   attribute (CSS does the actual hiding) and syncs ?var= in the URL.
   Without this script the CSS default (body[data-active-variant] set by
   Hugo) keeps the page usable. */
(function () {
  "use strict";

  function activeVariant() {
    return document.body.getAttribute("data-active-variant");
  }

  function setVariant(name) {
    var block = document.querySelector('[data-variant-block="' + name + '"]');
    if (!block) return;
    document.body.setAttribute("data-active-variant", name);
    var label = document.querySelector(".version-menu .variant-label");
    var link = document.querySelector('.version-menu a[data-variant="' + name + '"]');
    if (label && link) label.textContent = link.textContent;
    var url = new URL(window.location.href);
    var fallback = document.body.getAttribute("data-default-variant");
    if (name === fallback) url.searchParams.delete("var");
    else url.searchParams.set("var", name);
    history.replaceState(null, "", url);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var requested = new URL(window.location.href).searchParams.get("var");
    if (requested && requested !== activeVariant()) setVariant(requested);

    var menu = document.querySelector(".version-menu");
    if (!menu) return;
    menu.addEventListener("click", function (event) {
      var link = event.target.closest("a[data-variant]");
      if (!link) return;
      event.preventDefault();
      setVariant(link.getAttribute("data-variant"));
      menu.removeAttribute("open");
    });
  });
})();
