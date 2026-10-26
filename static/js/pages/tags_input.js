// Tag chip input on the item form. Existing tags are rendered server-side as chips
// (each with a hidden `tags` input) so they submit even without JS; here we let you add
// more (Enter or comma) and remove any. Every tag used before is offered as a tappable
// pill under the box (.js-tag-suggest, server-rendered), filtered live as you type —
// that's the autocomplete, done with real buttons because <datalist> suggestions never
// surface on mobile Safari. Names are normalised (trim + lower-case) to match the
// server and avoid case-dupes.
(function () {
    "use strict";

    var wrap = document.querySelector(".js-tags");
    if (!wrap) { return; }
    var chips = wrap.querySelector(".js-tag-chips");
    var entry = wrap.querySelector(".js-tag-entry");
    if (!chips || !entry) { return; }
    var suggest = document.querySelector(".js-tag-suggest");

    function normalize(s) { return (s || "").trim().toLowerCase(); }

    function current() {
        return Array.prototype.map.call(
            chips.querySelectorAll('input[name="tags"]'),
            function (i) { return i.value; }
        );
    }

    // which suggestions to offer: not already applied, and matching what's typed.
    // The box keeps its height no matter what (a fixed, scrolling strip in CSS) and
    // just says so when nothing qualifies — collapsing it used to shrink the page
    // under the keyboard and slide the entry away as you typed.
    var suggestNone = suggest ? suggest.querySelector(".js-tag-suggest-none") : null;
    function syncSuggest() {
        if (!suggest) { return; }
        var have = current();
        var q = normalize(entry.value);
        var any = false;
        Array.prototype.forEach.call(suggest.querySelectorAll(".tag-pill"), function (b) {
            var name = b.getAttribute("data-tag");
            var show = have.indexOf(name) === -1 && (!q || name.indexOf(q) !== -1);
            b.hidden = !show;
            if (show) { any = true; }
        });
        if (suggestNone) { suggestNone.hidden = any; }
    }

    function addTag(raw) {
        var name = normalize(raw);
        if (!name) { return; }
        if (current().indexOf(name) !== -1) { return; }   // already there

        var li = document.createElement("li");
        li.className = "tag-chip";
        var label = document.createElement("span");
        label.className = "tag-chip__label";
        label.textContent = name;
        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "tags";
        hidden.value = name;
        var rm = document.createElement("button");
        rm.type = "button";
        rm.className = "tag-chip__rm js-tag-rm";
        rm.setAttribute("aria-label", "Remove " + name);
        rm.innerHTML = "&times;";
        li.appendChild(label);
        li.appendChild(hidden);
        li.appendChild(rm);
        chips.appendChild(li);
        syncSuggest();
    }

    // the typeahead (shared helper from app.js): matching tags drop down as you
    // type, on top of the pills below. MUST be wired before the Enter handler
    // underneath — picking a highlighted suggestion stops the event so the
    // half-typed filter text doesn't also get committed as a tag.
    if (window.tagAutocomplete && suggest) {
        window.tagAutocomplete(
            entry,
            Array.prototype.map.call(suggest.querySelectorAll(".tag-pill"), function (b) {
                return b.getAttribute("data-tag");
            }),
            {
                query: function (v) { return v; },
                taken: current,
                pick: function (name) {
                    addTag(name);
                    entry.value = "";
                    syncSuggest();
                }
            }
        );
    }

    entry.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();   // don't submit the form / type the comma
            addTag(entry.value);
            entry.value = "";
            syncSuggest();
        } else if (e.key === "Backspace" && !entry.value) {
            // quick delete: backspace on an empty box drops the last chip
            var last = chips.querySelector(".tag-chip:last-child");
            if (last) { last.parentNode.removeChild(last); }
            syncSuggest();
        }
    });

    // typing narrows the suggestion pills — the autocomplete part
    entry.addEventListener("input", syncSuggest);

    // adding a tag on blur means a half-typed one isn't silently lost on Save.
    // A tap on a suggestion never blurs (see pointerdown below), so this only fires
    // when you really leave the field.
    entry.addEventListener("blur", function () {
        if (entry.value) { addTag(entry.value); entry.value = ""; }
    });

    if (suggest) {
        // a tap on a pill must not steal focus off the entry — on a phone that would
        // close the keyboard every single pick. Same trick as the typeahead list.
        suggest.addEventListener("pointerdown", function (e) {
            if (e.target.closest(".tag-pill")) { e.preventDefault(); }
        });
        suggest.addEventListener("click", function (e) {
            var btn = e.target.closest(".tag-pill");
            if (!btn) { return; }
            addTag(btn.getAttribute("data-tag"));
            entry.value = "";
            syncSuggest();
        });
    }

    // remove buttons (existing + future) via delegation
    chips.addEventListener("click", function (e) {
        var btn = e.target.closest(".js-tag-rm");
        if (!btn) { return; }
        var chip = btn.closest(".tag-chip");
        if (chip) { chip.parentNode.removeChild(chip); }
        syncSuggest();   // a removed tag goes back on offer
    });

    syncSuggest();   // hide suggestions already applied (edit form prefill)
})();
