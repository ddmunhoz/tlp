// Filter pickers on the pantry page (Expiry, Tag). The server renders each row as a
// summary pill ("Tag · Any") plus a panel holding the full list of chip links.
// Without JS the panel is just visible, so you get the old plain row of chips. With
// JS the panel becomes a popover on desktop (bottom sheet on a phone) that opens off
// the pill. A row can carry a find-as-you-type box (tags do, years don't need one),
// so sixty-odd tags are two keystrokes away instead of three screens of pills.
(function () {
    "use strict";

    var wraps = Array.prototype.slice.call(document.querySelectorAll(".js-fpick"));
    if (!wraps.length) { return; }
    var phone = window.matchMedia("(max-width: 719px)");
    var canHover = window.matchMedia("(hover: hover)").matches;
    var openOne = null;   // the picker currently showing, if any

    // one click-away layer shared by every picker; also dims the page behind the sheet
    var backdrop = document.createElement("div");
    backdrop.className = "fpick__backdrop";
    backdrop.hidden = true;
    document.body.appendChild(backdrop);

    // same accent-strip + casefold as the search box, so "cafe" finds "Café"
    function fold(s) {
        return (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
    }

    function setup(wrap) {
        var btn = wrap.querySelector(".js-fpick-btn");
        var panel = wrap.querySelector(".js-fpick-panel");
        if (!btn || !panel) { return null; }
        var find = wrap.querySelector(".js-fpick-find");
        var none = wrap.querySelector(".js-fpick-none");
        var chips = Array.prototype.slice.call(panel.querySelectorAll(".fpick__chips .chip"));

        // from here on the CSS floats + hides the panel (see .fpick__panel.is-ready)
        panel.classList.add("is-ready");
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-label", btn.textContent.trim());
        btn.setAttribute("aria-expanded", "false");
        btn.setAttribute("aria-controls", panel.id);

        var me = { wrap: wrap, btn: btn, panel: panel, find: find };

        // narrow the chips to what's typed. "Any" has no data-opt so it only shows
        // while the box is empty — that way Enter always lands on a real option.
        function filter() {
            var q = find ? fold(find.value.trim()) : "";
            var shown = 0;
            chips.forEach(function (a) {
                var name = a.getAttribute("data-opt");
                var match = !q || (name && fold(name).indexOf(q) !== -1);
                a.hidden = !match;
                if (match) { shown += 1; }
            });
            if (none) { none.hidden = shown > 0; }
        }

        // desktop: anchor under the pill, keep it on screen. Phone: the CSS pins it
        // to the bottom, so any inline position left over from desktop must go.
        function place() {
            if (phone.matches) {
                panel.style.left = ""; panel.style.top = ""; panel.style.maxHeight = "";
                return;
            }
            var r = btn.getBoundingClientRect(), pad = 8, gap = 6;
            var left = r.left, top = r.bottom + gap;
            var pw = panel.offsetWidth;
            if (left + pw > window.innerWidth - pad) { left = window.innerWidth - pw - pad; }
            if (left < pad) { left = pad; }
            // never run off the bottom: cap the height and let the chips scroll inside
            panel.style.maxHeight = Math.max(180, window.innerHeight - top - pad) + "px";
            panel.style.left = left + "px";
            panel.style.top = top + "px";
        }

        me.open = function () {
            if (openOne && openOne !== me) { openOne.close(); }
            if (find) { find.value = ""; }
            filter();
            panel.classList.add("is-open");
            backdrop.hidden = false;
            btn.setAttribute("aria-expanded", "true");
            openOne = me;
            place();
            // on a phone the keyboard would jump up over the sheet, so only autofocus
            // the find box where there's a real keyboard
            if (find && canHover) { find.focus(); }
        };
        me.close = function () {
            if (openOne !== me) { return; }
            panel.classList.remove("is-open");
            backdrop.hidden = true;
            btn.setAttribute("aria-expanded", "false");
            openOne = null;
            // only if focus was inside the panel; don't yank it off a row the user tapped
            if (panel.contains(document.activeElement)) { btn.focus(); }
        };
        me.place = place;

        btn.addEventListener("click", function () { if (openOne === me) { me.close(); } else { me.open(); } });
        if (find) {
            find.addEventListener("input", filter);
            // Enter follows the first real option still showing — type "arr", Enter,
            // done. Nothing typed = nothing happens, so a stray Enter can't wipe the filter.
            find.addEventListener("keydown", function (e) {
                if (e.key !== "Enter") { return; }
                e.preventDefault();
                if (!find.value.trim()) { return; }
                var first = chips.filter(function (a) { return !a.hidden && a.getAttribute("data-opt"); })[0];
                if (first) { first.click(); }
            });
        }
        return me;
    }

    wraps.forEach(setup);

    backdrop.addEventListener("click", function () { if (openOne) { openOne.close(); } });
    document.addEventListener("keydown", function (e) {
        if (!openOne) { return; }
        if (e.key === "Escape") { var b = openOne.btn; openOne.close(); b.focus(); }
        // Tab out past the last chip closes it too, so keyboard users can't get lost
        if (e.key === "Tab") {
            setTimeout(function () {
                if (openOne && !openOne.panel.contains(document.activeElement)) { openOne.close(); }
            }, 0);
        }
    });
    window.addEventListener("resize", function () { if (openOne) { openOne.place(); } });
    // the popover is position:fixed, so a page scroll would leave it floating away
    // from its pill. Simplest honest answer: close it.
    window.addEventListener("scroll", function () { if (openOne && !phone.matches) { openOne.close(); } }, { passive: true });
})();
