// Meals builder — pick pantry items, set grams, and watch the calorie total and
// the man/woman/child gauges update live. On save we serialise the lines into a
// hidden "components" field the server stores.
(function () {
    "use strict";

    var root = document.querySelector(".meal-builder");
    if (!root) { return; }

    var targets = {
        man: parseFloat(root.getAttribute("data-cal-man")) || 0,
        woman: parseFloat(root.getAttribute("data-cal-woman")) || 0,
        child: parseFloat(root.getAttribute("data-cal-child")) || 0
    };

    // nutrient bars — one per snapshotted nutrient, driven live like the gauges.
    // Each carries its meal_items column, daily-adult target, and unit from the DOM.
    var NUTS = Array.prototype.map.call(root.querySelectorAll(".js-nbar"), function (el) {
        return {
            col: el.getAttribute("data-col"),
            attr: "data-" + el.getAttribute("data-col").replace(/_/g, "-"),
            target: parseFloat(el.getAttribute("data-target")) || 0,
            unit: el.getAttribute("data-unit") || "",
            el: el,
            fill: el.querySelector(".js-nbar-fill"),
            pct: el.querySelector(".js-nbar-pct"),
            sub: el.querySelector(".js-nbar-sub")
        };
    });

    // read a source's per-gram nutrient values (from a pick's data-attrs, or a
    // prefilled saved line) into a {column: value|null} map.
    function readNut(get) {
        var o = {};
        NUTS.forEach(function (n) {
            var v = parseFloat(get(n));
            o[n.col] = isNaN(v) ? null : v;
        });
        return o;
    }

    var linesEl = root.querySelector(".js-meal-lines");
    var emptyEl = root.querySelector(".js-meal-empty");
    var totalEl = root.querySelector(".js-total");
    var componentsEl = root.querySelector(".js-components");
    var form = root.querySelector(".meal-form");

    // --- add-item picker: click a result to add a line -------------------------
    var search = root.querySelector(".js-pick-search");
    var picks = root.querySelectorAll(".js-pick");

    if (search) {
        search.addEventListener("input", function () {
            var q = search.value.trim().toLowerCase();
            Array.prototype.forEach.call(picks, function (p) {
                var name = (p.getAttribute("data-name") || "").toLowerCase();
                p.style.display = (!q || name.indexOf(q) !== -1) ? "" : "none";
            });
        });
    }
    Array.prototype.forEach.call(picks, function (p) {
        p.addEventListener("click", function () {
            addLine({
                source: p.getAttribute("data-source") || "product",
                tag_id: p.getAttribute("data-tag-id") || "",
                name: p.getAttribute("data-name"),
                barcode: p.getAttribute("data-barcode") || "",
                kcal_per_g: parseFloat(p.getAttribute("data-kcalperg")) || 0,
                unit: p.getAttribute("data-unit") || "g",
                photo: p.getAttribute("data-photo") || "",
                grams: 100,
                nut: readNut(function (n) { return p.getAttribute(n.attr); })
            });
            recalc();
        });
    });

    // --- a meal line -----------------------------------------------------------
    function addLine(c) {
        var isTag = c.source === "tag";
        var li = document.createElement("li");
        li.className = "meal-line" + (isTag ? " meal-line--tag" : "");
        li.setAttribute("data-source", isTag ? "tag" : "product");
        li.setAttribute("data-tag-id", c.tag_id || "");
        li.setAttribute("data-name", c.name);
        li.setAttribute("data-barcode", c.barcode);
        li.setAttribute("data-kcalperg", c.kcal_per_g);
        li.setAttribute("data-unit", c.unit);
        li.setAttribute("data-photo", c.photo || "");
        NUTS.forEach(function (n) {
            var v = c.nut ? c.nut[n.col] : null;
            if (v !== null && v !== undefined && !isNaN(v)) {
                li.setAttribute(n.attr, v);   // absent attr = product lacked this fact
            }
        });
        var thumb = isTag
            ? '<span class="meal-line__thumb meal-line__thumb--tag">🏷</span>'
            : (c.photo
                ? '<img class="meal-line__thumb" src="' + c.photo + '" alt="">'
                : '<span class="meal-line__thumb meal-line__thumb--none"></span>');
        li.innerHTML =
            thumb +
            '<span class="meal-line__name"></span>' +
            '<span class="meal-line__qty">' +
              '<input type="number" class="js-grams" min="1" step="1" inputmode="numeric"> ' +
              '<span class="meal-line__unit"></span>' +
            '</span>' +
            '<span class="meal-line__kcal"><span class="js-line-kcal">0</span> kcal</span>' +
            '<button type="button" class="meal-line__rm js-rm" aria-label="Remove">&times;</button>';
        li.querySelector(".meal-line__name").textContent = c.name;
        li.querySelector(".meal-line__unit").textContent = c.unit;
        var g = li.querySelector(".js-grams");
        g.value = c.grams;
        g.addEventListener("input", recalc);
        li.querySelector(".js-rm").addEventListener("click", function () {
            li.parentNode.removeChild(li);
            recalc();
        });
        linesEl.appendChild(li);
    }

    // --- recompute totals + gauges + the serialised payload --------------------
    function recalc() {
        var lines = linesEl.querySelectorAll(".meal-line");
        var total = 0;
        var payload = [];
        var nutTotals = {};
        NUTS.forEach(function (n) { nutTotals[n.col] = 0; });
        Array.prototype.forEach.call(lines, function (li) {
            var kpg = parseFloat(li.getAttribute("data-kcalperg")) || 0;
            var grams = parseFloat(li.querySelector(".js-grams").value) || 0;
            var kcal = kpg * grams;
            total += kcal;
            li.querySelector(".js-line-kcal").textContent = Math.round(kcal);
            var isTag = li.getAttribute("data-source") === "tag";
            var comp = {
                source_type: isTag ? "tag" : "product",
                tag_id: isTag ? (parseInt(li.getAttribute("data-tag-id"), 10) || null) : null,
                name: li.getAttribute("data-name"),
                barcode: li.getAttribute("data-barcode"),
                grams: grams,
                kcal_per_g: kpg,
                unit: li.getAttribute("data-unit")
            };
            NUTS.forEach(function (n) {
                var raw = li.getAttribute(n.attr);
                var v = raw === null ? NaN : parseFloat(raw);
                comp[n.col] = isNaN(v) ? null : v;
                if (!isNaN(v)) { nutTotals[n.col] += v * grams; }
            });
            payload.push(comp);
        });

        totalEl.textContent = Math.round(total).toLocaleString();
        componentsEl.value = JSON.stringify(payload);
        emptyEl.style.display = lines.length ? "none" : "";

        Array.prototype.forEach.call(root.querySelectorAll(".js-gauge"), function (gauge) {
            var who = gauge.getAttribute("data-who");
            var target = targets[who] || 0;
            var pct = target > 0 ? (total / target * 100) : 0;
            gauge.querySelector(".js-gauge-pct").textContent = Math.round(pct) + "%";
            gauge.querySelector(".js-gauge-sub").textContent = Math.round(total).toLocaleString();
            var fill = gauge.querySelector(".js-gauge-fill");
            fill.style.width = Math.min(pct, 100) + "%";
            // under 80% = low, 80–110% = on target, over = too much
            gauge.classList.remove("is-low", "is-ok", "is-over");
            gauge.classList.add(pct < 80 ? "is-low" : (pct <= 110 ? "is-ok" : "is-over"));
        });

        // nutrient bars: share of an adult's daily target. "Get enough" nutrients,
        // so no over-penalty — just cap the fill and flag once the day is met.
        NUTS.forEach(function (n) {
            var totalN = nutTotals[n.col] || 0;
            var pct = n.target > 0 ? (totalN / n.target * 100) : 0;
            n.fill.style.width = Math.min(pct, 100) + "%";
            n.pct.textContent = Math.round(pct) + "%";
            n.sub.textContent = Math.round(totalN) + " " + n.unit;
            n.el.classList.toggle("is-met", pct >= 100);
        });
    }

    // --- edit mode: prefill from the saved meal --------------------------------
    // find a product photo for a prefilled line by matching the picker (meal_items
    // don't store a photo of their own).
    function photoFor(barcode, name) {
        for (var i = 0; i < picks.length; i++) {
            var p = picks[i];
            if ((barcode && p.getAttribute("data-barcode") === barcode) ||
                (!barcode && p.getAttribute("data-name") === name)) {
                return p.getAttribute("data-photo") || "";
            }
        }
        return "";
    }
    var data = root.querySelector(".js-meal-data");
    if (data) {
        try {
            JSON.parse(data.textContent).forEach(function (it) {
                var isTag = it.source_type === "tag";
                addLine({
                    source: isTag ? "tag" : "product", tag_id: it.tag_id || "",
                    name: it.name, barcode: it.barcode || "",
                    kcal_per_g: it.kcal_per_g, unit: it.unit || "g", grams: it.grams,
                    photo: isTag ? "" : photoFor(it.barcode || "", it.name),
                    nut: readNut(function (n) { return it[n.col]; })
                });
            });
        } catch (e) { /* malformed prefill — start empty */ }
    }

    // block submit with no lines (the name-required attr covers the name)
    if (form) {
        form.addEventListener("submit", function (e) {
            if (!linesEl.querySelectorAll(".meal-line").length) {
                e.preventDefault();
                alert("Add at least one item to the meal.");
            }
        });
    }

    recalc();

    // --- saved menu: hover a card to reveal its ingredients --------------------
    // popover is position:fixed on <body>, placed next to the card and flipped /
    // nudged to stay on-screen, so it can't clip at the page edge.
    if (!window.matchMedia || window.matchMedia("(hover: hover)").matches) {
        Array.prototype.forEach.call(document.querySelectorAll(".js-menu-card"), function (card) {
            var pop = card.querySelector(".menu-pop");
            if (!pop) { return; }
            document.body.appendChild(pop);
            card.addEventListener("mouseenter", function () {
                pop.hidden = false;
                placePop(card, pop);
            });
            card.addEventListener("mouseleave", function () { pop.hidden = true; });
        });
    }

    function placePop(card, pop) {
        var r = card.getBoundingClientRect();
        var pw = pop.offsetWidth, ph = pop.offsetHeight, gap = 10, pad = 8;
        // prefer below-left-aligned; flip above if it would run off the bottom
        var left = Math.min(r.left, window.innerWidth - pw - pad);
        if (left < pad) { left = pad; }
        var top = r.bottom + gap;
        if (top + ph > window.innerHeight - pad) { top = r.top - ph - gap; }
        if (top < pad) { top = pad; }
        pop.style.left = left + "px";
        pop.style.top = top + "px";
    }
})();


// --- shared pantry planner --------------------------------------------------
// Dial servings per meal; the pooled stock reacts live. Meals sharing an
// ingredient draw from the same pool, so committing one shrinks the others'
// headroom. The count is persisted server-side (shared across everyone) via a
// debounced POST. "room for X more" is a marginal figure (others held as they
// are) — the usage bars are the source of truth for the whole plan fitting.
(function () {
    "use strict";

    var root = document.querySelector(".js-plan");
    if (!root) { return; }
    var dataEl = root.querySelector(".js-plan-data");
    if (!dataEl) { return; }

    var data;
    try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
    var stock = data.stock || {};
    var names = data.names || {};
    var people = data.people || {};
    var heads = (people.men || 0) + (people.women || 0) + (people.children || 0);
    // meals a head eats a day (settings). the household burns heads × this many
    // servings per day, so that's the divisor behind every "days" figure here.
    var mealsPerDay = Math.max(1, parseInt(data.meals_per_day, 10) || 1);
    var perDay = heads * mealsPerDay;
    var byId = {};
    (data.meals || []).forEach(function (m) { byId[m.id] = m; });

    var usageWrap = root.querySelector(".js-plan-usage");
    var barsEl = root.querySelector(".js-plan-bars");
    var verdictEl = root.querySelector(".js-plan-verdict");
    var basisEl = root.querySelector(".js-plan-basis");
    var bomEl = root.querySelector(".js-plan-bom");
    var bomListEl = root.querySelector(".js-plan-bom-list");

    // a shortfall amount for the BOM: grams, rolled up to kg once it's big
    function fmtShort(g) {
        g = Math.round(g);
        if (g >= 1000) { return (Math.round(g / 100) / 10) + " kg"; }
        return g + " g";
    }

    // "how many days a batch of servings lasts" — one serving is one person's
    // portion, so the household gets through `perDay` servings a day. Purely
    // ingredient-and-people; no calorie basis.
    function humanDays(servings) {
        if (!heads) { return null; }          // no household set up
        var d = servings / perDay;
        var n;
        if (d >= 10) { n = Math.round(d); }
        else if (d <= 0) { n = 0; }
        else if (d < 0.05) { return "under 0.1 day"; }
        else { n = Math.round(d * 10) / 10; }
        return n + (n === 1 ? " day" : " days");   // "1 day", not "1 days"
    }

    // Servings of a meal this meal could make on its own from the full pantry —
    // its standalone ceiling, ignoring what other meals also claim.
    function maxServings(m) {
        var cap = Infinity;
        m.components.forEach(function (c) {
            var avail = stock[c.key] || 0;
            var fit = c.grams > 0 ? Math.floor(avail / c.grams) : Infinity;
            if (fit < cap) { cap = fit; }
        });
        return cap === Infinity ? 0 : Math.max(0, cap);
    }

    // The one-line basis caption: who the "lasts X days" figures assume eats.
    (function () {
        if (!basisEl) { return; }
        var bits = [];
        if (people.men) { bits.push(people.men + (people.men === 1 ? " man" : " men")); }
        if (people.women) { bits.push(people.women + (people.women === 1 ? " woman" : " women")); }
        if (people.children) { bits.push(people.children + (people.children === 1 ? " child" : " children")); }
        if (!heads) {
            basisEl.textContent = "Set your household in Settings to see how many days each meal lasts.";
        } else {
            basisEl.textContent = "Days assume a portion each for " + bits.join(" · ") +
                " (" + heads + (heads === 1 ? " person" : " people") + ") — " +
                (mealsPerDay === 1 ? "one meal" : mealsPerDay + " meals") + " a day.";
        }
        basisEl.hidden = false;
    })();

    // planned servings live in each stepper's input; wire the controls.
    var steps = Array.prototype.slice.call(root.querySelectorAll(".js-plan-step"));
    var timers = {};

    steps.forEach(function (step) {
        var id = parseInt(step.getAttribute("data-meal-id"), 10);
        var input = step.querySelector(".js-plan-n");
        var dec = step.querySelector(".js-plan-dec");
        var inc = step.querySelector(".js-plan-inc");
        function bump(d) {
            input.value = Math.max(0, (parseInt(input.value, 10) || 0) + d);
            changed(id, input);
        }
        dec.addEventListener("click", function () { bump(-1); });
        inc.addEventListener("click", function () { bump(1); });
        input.addEventListener("input", function () { changed(id, input); });
    });

    function changed(id, input) {
        var n = Math.max(0, parseInt(input.value, 10) || 0);
        recompute();
        // debounce the shared save so a held +/- doesn't spam the server
        clearTimeout(timers[id]);
        timers[id] = setTimeout(function () { persist(id, n); }, 350);
    }

    function persist(id, n) {
        var body = new URLSearchParams();
        body.set("servings", n);
        fetch("/meals/" + id + "/plan", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString()
        }).catch(function () { /* offline is fine — the number stays on screen */ });
    }

    function planned(id) {
        var step = root.querySelector('.js-plan-step[data-meal-id="' + id + '"]');
        var input = step && step.querySelector(".js-plan-n");
        return input ? Math.max(0, parseInt(input.value, 10) || 0) : 0;
    }

    var menuServingsEl = document.querySelector(".js-menu-servings");
    var menuDaysEl = document.querySelector(".js-menu-days");

    function recompute() {
        // total demand per ingredient across every meal's current plan
        var demand = {};
        var seen = {};
        var totalServings = 0;
        (data.meals || []).forEach(function (m) {
            var n = planned(m.id);
            totalServings += n;
            m.components.forEach(function (c) {
                seen[c.key] = names[c.key] || c.name;
                demand[c.key] = (demand[c.key] || 0) + n * c.grams;
            });
        });

        // keep the header tally (servings + days) live as steppers change
        if (menuServingsEl) { menuServingsEl.textContent = totalServings; }
        if (menuDaysEl && heads) { menuDaysEl.textContent = Math.round(totalServings / perDay); }

        // per-meal "room for X more", holding the others where they are
        steps.forEach(function (step) {
            var id = parseInt(step.getAttribute("data-meal-id"), 10);
            var m = byId[id];
            var room = Infinity;
            m.components.forEach(function (c) {
                var avail = stock[c.key] || 0;
                var left = avail - (demand[c.key] || 0);
                var fit = c.grams > 0 ? Math.floor(left / c.grams) : Infinity;
                if (fit < room) { room = fit; }
            });
            room = Math.max(0, room === Infinity ? 0 : room);
            var roomEl = step.querySelector(".js-plan-room");
            roomEl.textContent = room > 0 ? "+" + room : "0";
            roomEl.title = room > 0
                ? "room for " + room + " more serving" + (room === 1 ? "" : "s") + " from stock"
                : "no more servings from current stock";
            step.classList.toggle("is-full", room === 0);

            // "lasts X days" — planned servings ÷ (household × meals a day) once
            // you've dialled some in; at rest, the standalone stock ceiling as the
            // potential. A serving is one person's portion.
            var feedsEl = step.parentElement.querySelector(".js-plan-feeds");
            if (feedsEl) {
                var n = planned(id);
                var basis = n > 0 ? n : maxServings(m);
                var days = humanDays(basis);
                var divisor = "(" + heads + (heads === 1 ? " person" : " people") +
                    " × " + mealsPerDay + (mealsPerDay === 1 ? " meal" : " meals") + " a day)";
                if (days === null) {
                    feedsEl.textContent = "";
                } else if (n > 0) {
                    feedsEl.textContent = "lasts ~" + days;
                    feedsEl.title = n + " serving" + (n === 1 ? "" : "s") + " ÷ " +
                        divisor + " ≈ " + days;
                } else {
                    feedsEl.textContent = "stock: " + basis + " → ~" + days;
                    feedsEl.title = "stock makes up to " + basis + " serving" +
                        (basis === 1 ? "" : "s") + " ÷ " + divisor + " ≈ " + days;
                }
                feedsEl.classList.toggle("is-muted", n === 0);
            }
        });

        // usage bars for every ingredient any meal uses
        var keys = Object.keys(seen).sort(function (a, b) {
            return (seen[a] || "").localeCompare(seen[b] || "");
        });
        var over = [];
        var bom = [];   // {name, short} for each ingredient the plan runs short on
        barsEl.innerHTML = "";
        keys.forEach(function (k) {
            var avail = stock[k] || 0;
            var used = demand[k] || 0;
            var pct = avail > 0 ? (used / avail * 100) : (used > 0 ? 999 : 0);
            var isOver = used > avail + 0.001;
            if (isOver) {
                over.push((seen[k] || k) + " " + Math.round(used - avail) + " g");
                bom.push({ name: seen[k] || k, short: used - avail });
            }
            var bar = document.createElement("div");
            bar.className = "plan-bar" + (isOver ? " is-over" : "");
            bar.innerHTML =
                '<span class="plan-bar__lbl"></span>' +
                '<span class="plan-bar__track"><span class="plan-bar__fill"></span></span>' +
                '<span class="plan-bar__val"></span>';
            bar.querySelector(".plan-bar__lbl").textContent = seen[k] || k;
            bar.querySelector(".plan-bar__fill").style.width = Math.min(pct, 100) + "%";
            bar.querySelector(".plan-bar__val").textContent =
                Math.round(used) + " / " + Math.round(avail) + " g";
            barsEl.appendChild(bar);
        });

        var anyPlanned = keys.some(function (k) { return (demand[k] || 0) > 0; });
        usageWrap.hidden = !keys.length;
        if (!anyPlanned) {
            verdictEl.textContent = "Nothing planned yet";
            verdictEl.className = "plan-verdict js-plan-verdict";
        } else if (over.length) {
            verdictEl.textContent = "Over by: " + over.join(" · ");
            verdictEl.className = "plan-verdict js-plan-verdict is-over";
        } else {
            verdictEl.textContent = "Plan fits your stock ✓";
            verdictEl.className = "plan-verdict js-plan-verdict is-ok";
        }

        // BOM — the shopping list. Pops up only when the plan outstrips stock, sorted
        // by biggest shortfall first, telling you how much of each to buy.
        if (bomEl && bomListEl) {
            bom.sort(function (a, b) { return b.short - a.short; });
            bomListEl.innerHTML = "";
            bom.forEach(function (item) {
                var li = document.createElement("li");
                li.className = "plan-bom__item";
                li.innerHTML = '<span class="plan-bom__name"></span>' +
                               '<span class="plan-bom__amt"></span>';
                li.querySelector(".plan-bom__name").textContent = item.name;
                li.querySelector(".plan-bom__amt").textContent = "buy " + fmtShort(item.short);
                bomListEl.appendChild(li);
            });
            bomEl.hidden = bom.length === 0;
        }
    }

    recompute();
})();
