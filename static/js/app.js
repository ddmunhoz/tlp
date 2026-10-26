// Shared front-end helpers. Nothing heavy — a confirm guard on destructive forms,
// remembering which shelves are folded shut, and the multi-select move bar.
(function () {
    "use strict";

    // --- confirm guard so a stray tap doesn't nuke an item ---
    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (form.classList && form.classList.contains("js-confirm")) {
            var msg = form.getAttribute("data-confirm") || "Are you sure?";
            if (!window.confirm(msg)) {
                event.preventDefault();
            }
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        rememberShelfState();
        wireSelection();
        wireSort();
    });

    // --- click a column header to sort that shelf's rows, flip on re-click ---
    // Delegated, resolving the shelf's row list at click time: AJAX pagination and the
    // bar's soft refresh replace .rows/.shelf nodes, so anything captured at load would
    // be a detached list and sorting would silently do nothing.
    function wireSort() {
        document.addEventListener("click", function (e) {
            var btn = e.target.closest(".colhead");
            if (!btn) { return; }
            var head = btn.closest(".rows__head");
            var shelf = btn.closest(".shelf");
            var list = shelf ? shelf.querySelector(".rows") : null;
            if (!head || !list) { return; }
            var dir = btn.getAttribute("data-dir") === "asc" ? "desc" : "asc";
            // only one column shows an arrow at a time
            Array.prototype.forEach.call(head.querySelectorAll(".colhead"), function (b) {
                b.removeAttribute("data-dir");
            });
            btn.setAttribute("data-dir", dir);
            sortRows(list, btn.getAttribute("data-sort"), dir);
        });
    }

    function sortRows(list, key, dir) {
        var rows = Array.prototype.slice.call(list.querySelectorAll(".row"));
        var numeric = key !== "name";
        rows.sort(function (a, b) {
            var av = a.getAttribute("data-" + key);
            var bv = b.getAttribute("data-" + key);
            if (numeric) { av = parseFloat(av); bv = parseFloat(bv); }
            if (av < bv) { return dir === "asc" ? -1 : 1; }
            if (av > bv) { return dir === "asc" ? 1 : -1; }
            return 0;
        });
        rows.forEach(function (r) { list.appendChild(r); });
    }

    // --- collapsible shelves: keep each one's open/closed state across reloads ---
    // The saved state is applied both at load and after the pantry's soft refresh
    // swaps in fresh .shelf nodes (which would otherwise reset to the template
    // default). Persisting uses one delegated listener — 'toggle' doesn't bubble, so
    // it's captured — instead of per-element handlers that die with the old nodes.
    function rememberShelfState() {
        applyShelfState();
        document.addEventListener("pantry:rows-changed", applyShelfState);
        document.addEventListener("toggle", function (e) {
            var shelf = e.target;
            if (!shelf.matches || !shelf.matches(".shelf[data-shelf-id]")) { return; }
            if (shelf.dataset.restoring) { return; }   // programmatic re-apply, not the user
            try {
                window.localStorage.setItem(
                    "shelf-open:" + shelf.getAttribute("data-shelf-id"),
                    shelf.open ? "open" : "closed");
            } catch (err) { /* private mode / no storage — just don't persist */ }
        }, true);
    }

    function applyShelfState() {
        Array.prototype.forEach.call(document.querySelectorAll(".shelf[data-shelf-id]"), function (shelf) {
            var stored = null;
            try {
                stored = window.localStorage.getItem("shelf-open:" + shelf.getAttribute("data-shelf-id"));
            } catch (e) { stored = null; }
            if (stored !== "closed" && stored !== "open") { return; }
            shelf.dataset.restoring = "1";   // don't let the re-apply overwrite the store
            shelf.open = stored === "open";
            delete shelf.dataset.restoring;
        });
    }

    // --- multi-select move: show the bar when rows are picked, wire select-all ---
    // Everything here is delegated / uses live queries, so rows swapped in by AJAX
    // pagination just work — no per-row re-wiring needed.
    function wireSelection() {
        var bar = document.getElementById("move-form");
        if (!bar) { return; }
        var counter = bar.querySelector(".js-move-count");
        var items = function () { return document.querySelectorAll(".js-item"); };

        function refresh() {
            var n = 0;
            Array.prototype.forEach.call(items(), function (b) { if (b.checked) { n += 1; } });
            if (counter) { counter.textContent = n; }
            bar.hidden = n === 0;
            syncSelectAll();
        }

        function syncSelectAll() {
            Array.prototype.forEach.call(document.querySelectorAll(".js-select-all"), function (all) {
                var rows = shelfBoxes(all);
                var total = rows.length;
                var checked = 0;
                Array.prototype.forEach.call(rows, function (b) { if (b.checked) { checked += 1; } });
                all.checked = total > 0 && checked === total;
                all.indeterminate = checked > 0 && checked < total;
            });
        }

        function cancelSelection() {
            if (bar.hidden) { return; }
            Array.prototype.forEach.call(items(), function (b) { b.checked = false; });
            refresh();
        }

        // one delegated change handler covers item ticks + each shelf's select-all
        document.addEventListener("change", function (e) {
            var t = e.target;
            if (!t.classList) { return; }
            if (t.classList.contains("js-item")) {
                refresh();
            } else if (t.classList.contains("js-select-all")) {
                Array.prototype.forEach.call(shelfBoxes(t), function (b) { b.checked = t.checked; });
                refresh();
            }
        });

        // Esc, or a click/tap outside the bar (and not on a selection control), cancels.
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") { cancelSelection(); }
        });
        document.addEventListener("pointerdown", function (e) {
            if (bar.hidden) { return; }
            // .tag-ac = the tag typeahead's floating list (it lives on <body>, not
            // in the bar) — picking a suggestion must not cancel the selection
            if (e.target.closest("#move-form, .row__sel, .rows__head-sel, .selectall, .tag-ac")) { return; }
            cancelSelection();
        });

        // AJAX pagination fires this after swapping a shelf's rows, so the bar count +
        // select-all state re-sync to whatever's now on the page.
        document.addEventListener("pantry:rows-changed", refresh);

        wireActions(bar);
        wireTagSuggest(bar);
        refresh();
    }

    // the Tag action's suggestion pills: tapping toggles the tag in the bar's
    // comma-separated field (mobile has no datalist autocomplete, so existing tags
    // are offered as real buttons). Typing in the field keeps the pills in step.
    function wireTagSuggest(bar) {
        var box = bar.querySelector(".js-movebar-suggest");
        var input = bar.querySelector('input[name="tags"]');
        if (!box || !input) { return; }

        function names() {
            return input.value.split(",").map(function (s) {
                return s.trim().toLowerCase();
            }).filter(function (s) { return s; });
        }

        function sync() {
            var have = names();
            Array.prototype.forEach.call(box.querySelectorAll(".tag-pill"), function (b) {
                b.classList.toggle("is-on", have.indexOf(b.getAttribute("data-tag")) !== -1);
            });
        }

        box.addEventListener("click", function (e) {
            var btn = e.target.closest(".tag-pill");
            if (!btn) { return; }
            var t = btn.getAttribute("data-tag");
            var have = names();
            var i = have.indexOf(t);
            if (i === -1) { have.push(t); } else { have.splice(i, 1); }
            // trailing ", " starts a fresh fragment, so typing right after a tap
            // autocompletes a NEW tag instead of mangling the last one
            input.value = have.length ? have.join(", ") + ", " : "";
            sync();
        });
        input.addEventListener("input", sync);
        sync();

        // and a typeahead on the fragment after the last comma: the pills offer
        // the vocabulary at a glance, this completes a half-typed (or rare) tag.
        function committed() {
            var parts = input.value.split(",");
            parts.pop();   // the fragment being typed
            return parts.map(function (s) { return s.trim(); })
                .filter(function (s) { return s; });
        }
        var ac = window.tagAutocomplete(
            input,
            Array.prototype.map.call(box.querySelectorAll(".tag-pill"), function (b) {
                return b.getAttribute("data-tag");
            }),
            {
                query: function (v) { return v.split(",").pop(); },
                taken: committed,
                pick: function (name) {
                    var parts = committed();
                    parts.push(name);
                    input.value = parts.join(", ") + ", ";   // primed for the next tag
                    sync();
                }
            }
        );
        bar.addEventListener("submit", ac.close);   // don't leave the list floating
    }

    // the bar carries one action at a time: Move / Tag / Delete. Picking one swaps
    // the visible context (shelf select / tag input / a warning) and retargets the
    // single confirm button — so Delete is a deliberate two-step, never a stray click.
    function wireActions(bar) {
        var acts = bar.querySelectorAll(".movebar__act");
        var go = bar.querySelector(".js-movebar-go");
        if (!acts.length || !go) { return; }
        // a context can be several elements (the tag action = its field + the
        // suggestion pills), so each key holds a list and we toggle them all.
        var ctx = {
            move: bar.querySelectorAll(".js-ctx-move"),
            tag: bar.querySelectorAll(".js-ctx-tag"),
            delete: bar.querySelectorAll(".js-ctx-delete")
        };
        var CONF = {
            move:   { url: bar.getAttribute("data-move-url"),   label: "Move",   danger: false },
            tag:    { url: bar.getAttribute("data-tag-url"),    label: "Tag",    danger: false },
            delete: { url: bar.getAttribute("data-delete-url"), label: "Delete", danger: true }
        };
        var current = "move";

        function setAction(name) {
            if (!CONF[name]) { return; }
            current = name;
            Array.prototype.forEach.call(acts, function (a) {
                a.classList.toggle("is-on", a.getAttribute("data-action") === name);
            });
            // toggle the `hidden` attribute; the .movebar__to[hidden] CSS rule makes it
            // actually hide (a plain [hidden] lost to .movebar__to's display:inline-flex).
            Object.keys(ctx).forEach(function (k) {
                Array.prototype.forEach.call(ctx[k], function (el) {
                    el.hidden = (k !== name);
                });
            });
            var cfg = CONF[name];
            go.textContent = cfg.label;
            // set both so a click OR Enter-in-a-field hits the right endpoint
            go.setAttribute("formaction", cfg.url);
            bar.setAttribute("action", cfg.url);
            go.classList.toggle("btn--primary", !cfg.danger);
            go.classList.toggle("btn--danger", cfg.danger);
        }

        Array.prototype.forEach.call(acts, function (a) {
            a.addEventListener("click", function () { setAction(a.getAttribute("data-action")); });
        });

        // confirm only the destructive path, and only when something's selected
        go.addEventListener("click", function (e) {
            if (current !== "delete") { return; }
            var n = document.querySelectorAll(".js-item:checked").length;
            if (!n) { return; }
            if (!window.confirm("Delete " + n + " selected item" + (n === 1 ? "" : "s") +
                                " from the pantry?")) {
                e.preventDefault();
            }
        });

        setAction("move");
    }

    // the item checkboxes living under the same shelf as a given select-all
    function shelfBoxes(all) {
        var shelf = all.closest(".shelf");
        return shelf ? shelf.querySelectorAll(".js-item") : [];
    }

    // --- tag autocomplete: a floating typeahead under a tag input --------------
    // A real autocomplete (native <datalist> never surfaces on mobile Safari): as
    // you type, matching tags drop down in a fixed-position list — JS-placed like
    // the app's popovers, flipping above the input when the move bar hugs the
    // bottom edge — and tap or ArrowDown/Enter completes the tag. It complements
    // the always-visible suggestion pills: pills for browsing, this for typing.
    // `query` extracts the fragment being typed from the field's value, `taken`
    // lists names to withhold, `pick` commits a chosen one. On window so the item
    // form's page script (tags_input.js) can reuse it. Returns {close}.
    window.tagAutocomplete = function (input, names, opts) {
        var box = document.createElement("ul");
        box.className = "tag-ac";
        box.hidden = true;
        document.body.appendChild(box);
        var items = [];
        var active = -1;

        function fold(s) { return (s || "").trim().toLowerCase(); }

        function close() { box.hidden = true; active = -1; }

        function place() {
            var r = input.getBoundingClientRect();
            box.style.left = r.left + "px";
            box.style.minWidth = r.width + "px";
            box.style.top = (r.bottom + 4) + "px";
            if (r.bottom + 4 + box.offsetHeight > window.innerHeight - 8) {
                box.style.top = (r.top - box.offsetHeight - 4) + "px";
            }
        }

        function render() {
            var q = fold(opts.query(input.value));
            if (!q) { close(); return; }   // pills cover browsing; this list is for typing
            var taken = (opts.taken ? opts.taken() : []).map(fold);
            items = names.filter(function (n) {
                return taken.indexOf(n) === -1 && n.indexOf(q) !== -1;
            }).slice(0, 8);
            if (!items.length) { close(); return; }
            if (active >= items.length) { active = items.length - 1; }
            box.innerHTML = "";
            items.forEach(function (n, i) {
                var li = document.createElement("li");
                li.className = "tag-ac__item" + (i === active ? " is-active" : "");
                li.textContent = n;
                li.setAttribute("data-name", n);
                box.appendChild(li);
            });
            box.hidden = false;
            place();
        }

        function pick(name) {
            opts.pick(name);
            active = -1;
            render();   // the pick empties the fragment, so this closes the list
        }

        input.addEventListener("input", render);
        input.addEventListener("blur", close);
        input.addEventListener("keydown", function (e) {
            if (box.hidden) { return; }
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                var step = e.key === "ArrowDown" ? 1 : -1;
                active = (active + step + items.length) % items.length;
                render();
            } else if (e.key === "Enter" && active >= 0) {
                // consume the key outright: the bar's form must not submit, and the
                // item form's own "Enter commits the typed text" handler (registered
                // after this one) must not fire on a picked suggestion
                e.preventDefault();
                e.stopImmediatePropagation();
                pick(items[active]);
            } else if (e.key === "Escape") {
                e.stopPropagation();   // just close the list — not the bar's selection
                close();
            }
        });
        // a tap on the list must not blur the input (blur would close it mid-pick)
        box.addEventListener("pointerdown", function (e) { e.preventDefault(); });
        box.addEventListener("click", function (e) {
            var li = e.target.closest(".tag-ac__item");
            if (li) { pick(li.getAttribute("data-name")); }
        });
        window.addEventListener("scroll", function () { if (!box.hidden) { place(); } }, true);

        return { close: close };
    };
})();
