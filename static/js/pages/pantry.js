// Pantry page behaviour: live name search + the desktop hover preview.
(function () {
    "use strict";

    keepScroll();
    liveSearch();
    thumbPreviews();
    ajaxPagination();
    barActions();
    // the bulk-delete confirm now lives with the move-bar action switcher (app.js),
    // since Delete is one of its actions rather than a standalone button.

    // --- keep the scroll position across the reloads bar actions + search cause ----
    // A bar action POSTs then redirects back here, and a search auto-submits — both are
    // full navigations that would otherwise dump you at the top of the list. We stash
    // the scroll offset just before the submit and restore it once the page comes back.
    function keepScroll() {
        var KEY = "pantryScrollY";
        // let us drive scroll ourselves; stops the browser fighting the restore
        if ("scrollRestoration" in history) { history.scrollRestoration = "manual"; }
        var saved = sessionStorage.getItem(KEY);
        if (saved !== null) {
            sessionStorage.removeItem(KEY);
            window.scrollTo(0, parseInt(saved, 10) || 0);
        }
        function remember() { sessionStorage.setItem(KEY, String(window.scrollY)); }
        // only the search still reloads; bar actions are AJAX (no navigation to survive)
        var searchForm = document.querySelector(".search__form");
        if (searchForm) { searchForm.addEventListener("submit", remember); }
    }

    // --- bulk bar actions (move / tag / delete) without a reload -----------------
    // Submit via fetch, then soft-refresh the list in place. The server returns JSON
    // for us (and still flash+redirects for a no-JS submit).
    function barActions() {
        var form = document.getElementById("move-form");
        if (!form) { return; }
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var url = form.getAttribute("action");   // setAction keeps this = current endpoint
            fetch(url, {
                method: "POST",
                body: new FormData(form),
                credentials: "same-origin",
                headers: { "X-Requested-With": "fetch" }
            })
                .then(function (r) { return r.json(); })
                .then(function (res) {
                    showFlash(res.message, res.ok ? "ok" : "error");
                    if (res.ok) { return softRefresh(); }   // reflect the change in place
                })
                .catch(function () { form.submit(); });   // fall back to a real submit on error
        });
    }

    // re-render the results list in place from the server (authoritative for move /
    // tag / delete / search + pagination), without navigating — so scroll, the
    // keyboard and the whole page stay. `url` defaults to the current page; the live
    // search passes the URL it would have submitted to.
    function softRefresh(url) {
        return fetch(url || window.location.href, { credentials: "same-origin" })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                var doc = new DOMParser().parseFromString(html, "text/html");
                var fresh = doc.querySelector(".js-pantry-list");
                var cur = document.querySelector(".js-pantry-list");
                if (!fresh || !cur) { return; }
                // outgoing rows' popovers were reparented onto <body>; clear them
                Array.prototype.forEach.call(document.querySelectorAll("body > .thumb-pop"),
                    function (p) { p.remove(); });
                cur.replaceWith(document.importNode(fresh, true));
                swapFilterbar(doc, document.querySelector(".js-pantry-list"));
                thumbPreviews(document.querySelector(".js-pantry-list"));
                document.dispatchEvent(new Event("pantry:rows-changed"));   // re-sync bar
            });
    }

    // the "Filtered — …" banner sits above the list and only exists while a filter is
    // on, so it's a replace / add / remove like the pager in swapShelf
    function swapFilterbar(doc, list) {
        var cur = document.querySelector(".filterbar");
        var fresh = doc.querySelector(".filterbar");
        fresh = fresh ? document.importNode(fresh, true) : null;
        if (cur && fresh) { cur.replaceWith(fresh); }
        else if (cur && !fresh) { cur.remove(); }
        else if (!cur && fresh && list) { list.parentNode.insertBefore(fresh, list); }
    }

    // a transient flash message, matching the server-rendered ones
    function showFlash(message, category) {
        if (!message) { return; }
        var list = document.querySelector(".flashes");
        if (!list) {
            list = document.createElement("ul");
            list.className = "flashes";
            var main = document.querySelector("main.content") || document.body;
            main.insertBefore(list, main.firstChild);
        }
        var li = document.createElement("li");
        li.className = "flash flash--" + (category || "ok");
        li.textContent = message;
        list.appendChild(li);
        setTimeout(function () { li.remove(); }, 4000);
    }

    // --- desktop hover preview: bigger photo + nutrition off the thumbnail ------
    // `root` limits wiring to a subtree — used to re-wire just the rows AJAX pagination
    // swaps in. Each popover is tagged with its shelf id so those can be cleaned up.
    function thumbPreviews(root) {
        // hover only — touch devices skip it entirely
        if (!window.matchMedia || !window.matchMedia("(hover: hover)").matches) { return; }
        var wraps = (root || document).querySelectorAll(".js-thumb");
        Array.prototype.forEach.call(wraps, function (wrap) {
            var pop = wrap.querySelector(".thumb-pop");
            if (!pop) { return; }
            var shelf = wrap.closest(".shelf");
            if (shelf) { pop.dataset.shelf = shelf.getAttribute("data-shelf-id") || "0"; }
            // Reparent the popover to <body>. Expired rows carry opacity:0.9, and any
            // opacity < 1 makes a stacking context that traps a position:fixed child —
            // so the preview would slide under later rows. Living on <body> keeps it
            // above everything, no matter what a row's opacity/transform does.
            document.body.appendChild(pop);
            wrap.addEventListener("mouseenter", function () {
                pop.hidden = false;
                place(wrap, pop);
            });
            wrap.addEventListener("mouseleave", function () { pop.hidden = true; });
        });
    }

    // position: fixed, so overflow:hidden on the shelf can't clip it. sit it to the
    // right of the thumb, flipping left / nudging up when it'd run off-screen.
    function place(wrap, pop) {
        var r = wrap.getBoundingClientRect();
        var pw = pop.offsetWidth, ph = pop.offsetHeight, gap = 12, pad = 8;
        var left = r.right + gap;
        if (left + pw > window.innerWidth - pad) { left = r.left - pw - gap; }
        if (left < pad) { left = pad; }
        var top = r.top;
        if (top + ph > window.innerHeight - pad) { top = window.innerHeight - ph - pad; }
        if (top < pad) { top = pad; }
        pop.style.left = left + "px";
        pop.style.top = top + "px";
    }

    // --- AJAX pagination: swap a shelf's rows in place, no full-page reload -------
    // Prev/Next used to be plain links -> full navigation -> lose your place. Now we
    // fetch the target page and splice just that shelf's rows + pager into the DOM.
    function ajaxPagination() {
        document.addEventListener("click", function (e) {
            var link = e.target.closest(".pager a");
            if (!link || !link.getAttribute("href")) { return; }   // disabled ends are <span>
            var shelf = link.closest(".shelf");
            if (!shelf) { return; }
            e.preventDefault();
            var shelfId = shelf.getAttribute("data-shelf-id");
            var href = link.href;
            fetch(href, { credentials: "same-origin" })
                .then(function (r) { return r.text(); })
                .then(function (html) {
                    var doc = new DOMParser().parseFromString(html, "text/html");
                    var fresh = doc.querySelector('.shelf[data-shelf-id="' + shelfId + '"]');
                    var cur = document.querySelector('.shelf[data-shelf-id="' + shelfId + '"]');
                    if (!fresh || !cur) { window.location.href = href; return; }   // fallback
                    swapShelf(cur, fresh, shelfId);
                    history.replaceState(null, "", href);   // URL tracks the page, no history spam
                })
                .catch(function () { window.location.href = href; });   // network error -> normal nav
        });
    }

    function swapShelf(cur, fresh, shelfId) {
        // drop the outgoing rows' hover popovers (they were reparented onto <body>)
        Array.prototype.forEach.call(
            document.querySelectorAll('.thumb-pop[data-shelf="' + shelfId + '"]'),
            function (p) { p.remove(); }
        );
        var curRows = cur.querySelector(".rows");
        var freshRows = fresh.querySelector(".rows");
        if (curRows && freshRows) { curRows.replaceWith(document.importNode(freshRows, true)); }

        // pager: replace / add / remove so it matches the page we landed on
        var curPager = cur.querySelector(".pager");
        var freshPager = fresh.querySelector(".pager");
        freshPager = freshPager ? document.importNode(freshPager, true) : null;
        if (curPager && freshPager) { curPager.replaceWith(freshPager); }
        else if (curPager && !freshPager) { curPager.remove(); }
        else if (!curPager && freshPager) { cur.appendChild(freshPager); }

        var curCount = cur.querySelector(".shelf__count");
        var freshCount = fresh.querySelector(".shelf__count");
        if (curCount && freshCount) { curCount.textContent = freshCount.textContent; }

        thumbPreviews(cur);   // wire hover previews on the new rows
        document.dispatchEvent(new Event("pantry:rows-changed"));   // re-sync bar + select-all
    }

    // --- live name search -------------------------------------------------------
    function liveSearch() {
    var input = document.querySelector(".search__input");
    if (!input) { return; }
    var chipLinks = document.querySelectorAll(".search__macros a");

    function syncChips(q) {
        Array.prototype.forEach.call(chipLinks, function (a) {
            var url = new URL(a.href, window.location.origin);
            if (q) { url.searchParams.set("q", q); } else { url.searchParams.delete("q"); }
            a.href = url.pathname + url.search;
        });
    }

    // strip accents + casefold so "cafe"/"Cafe" match "Café" — must mirror the
    // server's _fold so the live filter and a submitted search agree.
    function fold(s) {
        return (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    }

    function apply() {
        var q = fold(input.value.trim());
        var anyVisible = false;
        // query live each pass — softRefresh replaces the whole list, so anything
        // captured at load would be a detached node the filter mutates invisibly
        var shelves = Array.prototype.slice.call(document.querySelectorAll(".shelf"));
        var none = document.querySelector(".search__none");

        shelves.forEach(function (shelf) {
            var rows = shelf.querySelectorAll(".row");
            var shown = 0;
            Array.prototype.forEach.call(rows, function (row) {
                // data-search = folded name + tags, so typing a tag finds the item too
                var hay = row.getAttribute("data-search") || row.getAttribute("data-name") || "";
                var match = !q || hay.indexOf(q) !== -1;
                row.style.display = match ? "" : "none";
                if (match) { shown += 1; }
            });
            shelf.style.display = shown ? "" : "none";
            if (shown) { anyVisible = true; }

            var count = shelf.querySelector(".shelf__count");
            if (count) {
                if (!count.dataset.full) { count.dataset.full = count.textContent; }
                count.textContent = q ? (shown + " shown") : count.dataset.full;
            }
        });

        if (none) { none.hidden = !(q && !anyVisible); }
        syncChips(q);
    }

    // Instant client filter is only feedback on the rows already rendered — with
    // pagination, matches can live on other pages. So after a short pause we ask the
    // server, which searches the WHOLE pantry (accent-folded, name + tags), and swap
    // its results in place. Used to be a form submit, but a reload on every pause
    // drops the keyboard on a phone and throws you back to the top of the page —
    // now it's the same soft refresh the bulk actions use, plus a replaceState so
    // the URL still carries the query. Enter / the Search button still submit for real.
    var form = document.querySelector(".search__form");
    var timer;
    var seq = 0;   // a slow reply for "r" must not land on top of the result for "ri"
    function searchUrl() {
        var params = new URLSearchParams(new FormData(form));
        if (!params.get("q")) { params.delete("q"); }
        var qs = params.toString();
        return form.getAttribute("action") + (qs ? "?" + qs : "");
    }
    input.addEventListener("input", function () {
        apply();                                  // snappy narrowing of visible rows
        if (!form) { return; }
        clearTimeout(timer);
        timer = setTimeout(function () {
            var url = searchUrl();
            var mine = ++seq;
            softRefresh(url).then(function () {
                if (mine !== seq) { return; }
                history.replaceState(null, "", url);
                apply();   // fresh rows + the chips' hrefs back in step with the box
            });
        }, 350);
    });

    // returning from a search reload, drop the caret back in the box so typing flows
    if (input.value) {
        var v = input.value;
        input.focus();
        input.value = "";
        input.value = v;   // moves the caret to the end
    }
    }
})();
