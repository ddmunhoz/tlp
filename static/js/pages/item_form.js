// Add/edit item — barcode lookup against our Open Food Facts proxy, plus an
// optional live camera scan. Camera needs a secure context (https/localhost) AND
// a browser with BarcodeDetector; when either's missing we just hide Scan and lean
// on the manual field + Look up, which work everywhere.
(function () {
    "use strict";

    var form = document.querySelector(".item-form");
    if (!form) { return; }

    // label / key / unit, in label order — kept in step with _NUTRIMENTS server-side
    var NUTRIENT_ROWS = [
        ["Energy", "energy_kcal", "kcal"],
        ["Fat", "fat", "g"],
        ["Saturated fat", "saturated_fat", "g"],
        ["Trans fat", "trans_fat", "g"],
        ["Cholesterol", "cholesterol", "mg"],
        ["Sodium", "sodium", "mg"],
        ["Carbohydrates", "carbohydrates", "g"],
        ["Fibre", "fiber", "g"],
        ["Sugars", "sugars", "g"],
        ["Added sugars", "added_sugars", "g"],
        ["Protein", "proteins", "g"],
        ["Salt", "salt", "g"],
        ["Calcium", "calcium", "mg"],
        ["Iron", "iron", "mg"],
        ["Potassium", "potassium", "mg"]
    ];

    var lookupBase = form.getAttribute("data-lookup-base") || "/api/products/";
    var pantryBase = form.getAttribute("data-pantry-base");
    var barcode = document.getElementById("barcode");
    var status = form.querySelector(".scan__status");
    var scanBtn = form.querySelector(".js-scan");
    var lookupBtn = form.querySelector(".js-lookup");

    var nameEl = document.getElementById("name");
    var weightEl = document.getElementById("weight_value");
    var caloriesEl = document.getElementById("calories");
    var nutritionEl = document.getElementById("nutrition");
    var imageUrlEl = document.getElementById("image_url");
    var photoPathEl = document.getElementById("photo_path");
    var preview = document.getElementById("product-preview");
    var previewImg = preview.querySelector(".product__img");

    // --- auto-calorie: package kcal = per-100 energy × net weight / 100 ----------
    // Whenever the weight/unit changes and we know the product's per-100 energy
    // (from the OFF nutrition blob riding in the hidden field), fill the calories
    // field for the user. We back off the moment they type their own calories in.
    var caloriesTouched = false;

    function energyPer100() {
        try {
            var blob = JSON.parse(nutritionEl.value || "null");
            var e = blob && blob.per_100g && blob.per_100g.energy_kcal;
            return (e === 0 || e) ? parseFloat(e) : null;
        } catch (err) { return null; }
    }

    function baseGrams() {
        var v = parseFloat(weightEl.value);
        if (isNaN(v) || v <= 0) { return null; }
        var picked = form.querySelector('input[name="weight_unit"]:checked');
        var unit = picked ? picked.value : "g";
        return (unit === "kg" || unit === "l") ? v * 1000 : v;   // per-100 basis
    }

    function recalcCalories() {
        if (caloriesTouched) { return; }
        var e = energyPer100();
        var base = baseGrams();
        if (e === null || base === null) { return; }
        caloriesEl.value = Math.round(e * base / 100);
    }

    if (weightEl) { weightEl.addEventListener("input", recalcCalories); }
    Array.prototype.forEach.call(
        form.querySelectorAll('input[name="weight_unit"]'),
        function (r) { r.addEventListener("change", recalcCalories); }
    );
    // once they hand-edit calories, stop overwriting it from the energy math
    if (caloriesEl) {
        caloriesEl.addEventListener("input", function () { caloriesTouched = true; });
    }

    // --- fluid flag: auto-tick "counts as drinking fluid" for ml/L units --------
    var fluidEl = document.getElementById("is_fluid");
    var fluidTouched = false;

    function syncFluid() {
        if (fluidTouched || !fluidEl) { return; }
        var picked = form.querySelector('input[name="weight_unit"]:checked');
        var unit = picked ? picked.value : "";
        fluidEl.checked = (unit === "ml" || unit === "l");
    }

    Array.prototype.forEach.call(
        form.querySelectorAll('input[name="weight_unit"]'),
        function (r) { r.addEventListener("change", syncFluid); }
    );
    if (fluidEl) {
        fluidEl.addEventListener("change", function () { fluidTouched = true; });
    }

    // --- exclude-from-stats is the master switch: while it's on, the fluid flag is
    //     moot (the item is out of every stat), so fade + disable the fluid toggle.
    var excludeEl = document.getElementById("exclude_stats");
    var fluidPill = fluidEl ? fluidEl.closest(".itoggle") : null;

    function syncExclude() {
        if (!excludeEl || !fluidPill) { return; }
        // fade + block interaction, but DON'T disable the input — a disabled checkbox
        // isn't submitted, which would quietly wipe a saved is_fluid value. the pill's
        // pointer-events:none blocks clicks while the value rides along on save.
        fluidPill.classList.toggle("is-disabled", excludeEl.checked);
    }

    if (excludeEl) {
        excludeEl.addEventListener("change", syncExclude);
        syncExclude();   // reflect the saved state on load (e.g. editing an excluded item)
    }

    // --- calories-from-label helper: kcal_per_serving × package weight / serving --
    // For labels that give "90 kcal per 18 g" — scale that to the whole package.
    // The icon is only live once there's a weight to scale from (typed or from a
    // lookup); syncCalc() keeps that state + the panel's package-weight readout in
    // step, and fill() calls it after prefilling the weight.
    var calcEl = document.querySelector(".calc");
    var calcDo = document.getElementById("calc_do");
    var perServingEl = document.getElementById("calc_per_serving");
    var servingEl = document.getElementById("calc_serving");
    var calcUnitEl = form.querySelector(".calc__unit");
    var calcWeightEl = form.querySelector(".calc__weight");

    function syncCalc() {
        if (!calcEl) { return; }
        var picked = form.querySelector('input[name="weight_unit"]:checked');
        var unit = picked ? picked.value : "g";
        var basis = (unit === "kg") ? "g" : (unit === "l") ? "ml" : unit;
        if (calcUnitEl) { calcUnitEl.textContent = basis; }
        var base = baseGrams();
        var ready = base !== null;
        calcEl.classList.toggle("is-disabled", !ready);
        if (!ready) { calcEl.open = false; }   // can't open the panel without a weight
        if (calcWeightEl) { calcWeightEl.textContent = ready ? (base + " " + basis) : "—"; }
    }

    if (calcEl) {
        var calcToggle = calcEl.querySelector(".calc__toggle");
        if (calcToggle) {
            calcToggle.addEventListener("click", function (e) {
                if (calcEl.classList.contains("is-disabled")) { e.preventDefault(); }
            });
        }
        if (weightEl) { weightEl.addEventListener("input", syncCalc); }
        Array.prototype.forEach.call(
            form.querySelectorAll('input[name="weight_unit"]'),
            function (r) { r.addEventListener("change", syncCalc); }
        );
        if (calcDo) {
            calcDo.addEventListener("click", function () {
                var per = parseFloat(perServingEl.value);
                var serving = parseFloat(servingEl.value);
                var base = baseGrams();   // whole package, in g/ml
                if (isNaN(per) || per < 0) { say("Enter the calories per serving.", "warn"); return; }
                if (isNaN(serving) || serving <= 0) { say("Enter the serving size.", "warn"); return; }
                if (base === null) { say("Set the item weight first, then calculate.", "warn"); return; }
                caloriesEl.value = Math.round(per * base / serving);
                caloriesTouched = true;   // deliberate value — don't let energy math overwrite it
                calcEl.open = false;
                say("Calories worked out from the label.", "ok");
            });
        }
        syncCalc();   // initial state (disabled until a weight exists)
    }

    function say(msg, kind) {
        status.textContent = msg || "";
        status.className = "scan__status" + (kind ? " scan__status--" + kind : "");
    }

    // tick the matching weight-unit pill (the unit is a radio group now, not a select)
    function setUnit(u) {
        var radios = form.querySelectorAll('input[name="weight_unit"]');
        Array.prototype.forEach.call(radios, function (r) { r.checked = (r.value === u); });
    }

    // --- lookup ---------------------------------------------------------------
    function lookup(code) {
        code = (code || "").trim();
        if (!code) { say("Type or scan a barcode first."); return; }
        say("Looking up " + code + "…");
        fetch(lookupBase + encodeURIComponent(code))
            .then(function (r) {
                if (r.status === 404) { throw new Error("notfound"); }
                if (!r.ok) { throw new Error("http"); }
                return r.json();
            })
            .then(function (data) {
                if (!data.found) { throw new Error("notfound"); }
                var gotWeight = fill(data.product);
                say(gotWeight ? "Found it — check the details below."
                              : "Found it — add the weight + unit by hand.", "ok");
            })
            .catch(function (err) {
                if (err.message === "notfound") {
                    say("No match on Open Food Facts. Fill it in by hand.", "warn");
                } else {
                    say("Lookup failed (offline?). Fill it in by hand.", "warn");
                }
            });
    }

    function fill(product) {
        // A lookup is an explicit request for *this* product, so refresh the fields
        // to match it — otherwise a second scan on the same form leaves stale name/
        // weight from the first product while the facts show the new one.
        if (product.name && nameEl) { nameEl.value = product.name; }
        if (product.barcode && barcode) { barcode.value = product.barcode; }

        var gotWeight = false;
        if (product.weight_value != null && weightEl) {
            weightEl.value = product.weight_value;
            if (product.weight_unit) { setUnit(product.weight_unit); }
            gotWeight = true;
        } else if (weightEl && weightEl.value) {
            gotWeight = true;   // OFF had no quantity, keep whatever's there
        }
        // calories for the whole item — prefill, user can correct
        if (caloriesEl && product.calories != null) { caloriesEl.value = product.calories; }

        // these mirror the product exactly every time (no stale carry-over)
        nutritionEl.value = product.nutrition ? JSON.stringify(product.nutrition) : "";
        imageUrlEl.value = product.image_url || "";
        photoPathEl.value = "";   // a fresh lookup means we'll re-download the image
        caloriesTouched = false;  // fresh product — let the energy math drive again
        recalcCalories();         // fills calories now if OFF gave us a weight too
        fluidTouched = false;     // and re-decide the fluid flag from the new unit
        syncFluid();
        syncCalc();               // OFF filled the weight programmatically — light up the calculator
        renderPreview(product);
        return gotWeight;
    }

    function renderPreview(product) {
        preview.classList.remove("is-empty");
        if (product.image_url) {
            previewImg.src = product.image_url;
            previewImg.hidden = false;
        }
        // nutrition is standardised per 100 g (serving sizes are ignored)
        var n = product.nutrition || {};
        var facts = n.per_100g;
        var basis = "Per 100 g";
        var box = preview.querySelector(".product__facts");
        var html = "";
        var grade = n.nutriscore;
        if (grade) {
            html += '<span class="nutriscore nutriscore--' + grade + '">Nutri-Score ' +
                grade.toUpperCase() + "</span>";
        }
        if (facts && Object.keys(facts).length) {
            html += '<dl class="facts">';
            NUTRIENT_ROWS.forEach(function (r) {
                var v = facts[r[1]];
                if (v != null && v !== "") {
                    html += '<div class="facts__row"><dt>' + r[0] + "</dt><dd>" +
                        v + " " + r[2] + "</dd></div>";
                }
            });
            html += "</dl><p class='product__note'>" + basis + " · Data: Open Food Facts (ODbL)</p>";
        }
        box.innerHTML = html;
    }

    // A scanned code first asks our own shelves: already got it? jump to the quick
    // +/- screen. Otherwise carry on with the normal Open Food Facts fill.
    function resolveScan(code) {
        code = (code || "").trim();
        barcode.value = code;
        if (!pantryBase || !code) { lookup(code); return; }
        say("Checking the pantry…");
        fetch(pantryBase + encodeURIComponent(code))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (hit) {
                if (hit && hit.found && hit.url) { window.location.assign(hit.url); }
                else { lookup(code); }
            })
            .catch(function () { lookup(code); });   // offline? fall back to OFF/manual
    }

    lookupBtn.addEventListener("click", function () { lookup(barcode.value); });
    barcode.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); lookup(barcode.value); }
    });

    // deep link: /items/new?barcode=XXXX runs the lookup on load (handy after a scan)
    var qsBarcode = new URLSearchParams(window.location.search).get("barcode");
    if (qsBarcode) { barcode.value = qsBarcode; lookup(qsBarcode); }

    // --- quick expiry chips: tap "+3m" to set the date from today ---------------
    var expireEl = document.getElementById("expire_date");
    var chips = form.querySelectorAll(".chip[data-add]");

    function fromToday(spec) {
        var n = parseInt(spec, 10);
        var unit = spec.replace(/[0-9]/g, "");
        var d = new Date();
        if (unit === "d") { d.setDate(d.getDate() + n); }
        else if (unit === "m") { d.setMonth(d.getMonth() + n); }
        else if (unit === "y") { d.setFullYear(d.getFullYear() + n); }
        var mm = String(d.getMonth() + 1).padStart(2, "0");
        var dd = String(d.getDate()).padStart(2, "0");
        return d.getFullYear() + "-" + mm + "-" + dd;
    }

    Array.prototype.forEach.call(chips, function (chip) {
        chip.addEventListener("click", function () {
            if (expireEl) { expireEl.value = fromToday(chip.getAttribute("data-add")); }
            Array.prototype.forEach.call(chips, function (c) { c.classList.remove("is-on"); });
            chip.classList.add("is-on");
        });
    });
    // typing an exact date drops the chip highlight so they don't disagree
    if (expireEl) {
        expireEl.addEventListener("input", function () {
            Array.prototype.forEach.call(chips, function (c) { c.classList.remove("is-on"); });
        });
    }

    // --- live camera scan via ZXing -------------------------------------------
    // We decode with ZXing on every browser rather than the native BarcodeDetector:
    // iOS Safari exposes BarcodeDetector as a dead stub, so sniffing for it is a
    // trap. All we truly need is a secure context + getUserMedia, which iOS has
    // behind HTTPS. The ~336 KB library only loads on the first Scan tap.
    var canScan = window.isSecureContext &&
        navigator.mediaDevices && navigator.mediaDevices.getUserMedia;

    if (canScan) {
        scanBtn.hidden = false;
        wireCamera();
    }

    function wireCamera() {
        var cam = form.querySelector(".scan__cam");
        var video = form.querySelector(".scan__video");
        var cancel = form.querySelector(".js-scan-cancel");
        var zxingSrc = form.getAttribute("data-zxing-src");
        var reader = null;
        var stream = null;
        var loading = null;
        var active = false;

        function loadZxing() {
            if (window.ZXing) { return Promise.resolve(); }
            if (loading) { return loading; }
            loading = new Promise(function (resolve, reject) {
                var s = document.createElement("script");
                s.src = zxingSrc;
                s.onload = resolve;
                s.onerror = function () { reject(new Error("zxing-load")); };
                document.head.appendChild(s);
            });
            return loading;
        }
        // warm the library up in the background so the tap doesn't wait on a download
        loadZxing().catch(function () { /* surfaced later if they actually scan */ });

        function stop() {
            active = false;
            if (reader) {
                try { reader.reset(); } catch (e) { /* already torn down */ }
                reader = null;
            }
            if (stream) {
                stream.getTracks().forEach(function (t) { t.stop(); });
                stream = null;
            }
            cam.hidden = true;
            scanBtn.hidden = false;   // offer it again once we're done/cancelled
        }

        function start() {
            if (active) { return; }   // ignore mashing while the camera's coming up
            active = true;
            scanBtn.hidden = true;    // nothing to tap while it's live — use Cancel
            // iOS Safari only honours getUserMedia straight out of the tap, so open
            // the camera FIRST — before any await on the ZXing download — then hand
            // the live stream to the decoder.
            say("Starting the camera…");
            video.muted = true;
            video.setAttribute("playsinline", "");
            video.setAttribute("autoplay", "");
            navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
                .then(function (s) {
                    stream = s;
                    video.srcObject = s;
                    cam.hidden = false;
                    return video.play().catch(function () { /* iOS sometimes rejects, harmless */ });
                })
                .then(function () {
                    say("Loading scanner…");
                    return loadZxing();
                })
                .then(function () {
                    say("Point the camera at the barcode…");
                    reader = new window.ZXing.BrowserMultiFormatReader();
                    reader.decodeFromStream(stream, video, function (result) {
                        if (result) {
                            var code = result.getText ? result.getText() : result.text;
                            stop();
                            resolveScan(code);
                        }
                        // errors per-frame just mean "nothing readable yet" — ignore
                    });
                })
                .catch(function (err) {
                    stop();
                    var n = (err && (err.name || err.message)) || "error";
                    if (n === "NotAllowedError" || n === "SecurityError") {
                        say("Camera blocked. Allow it for this site in Safari, or type the barcode.", "warn");
                    } else if (n === "NotFoundError" || n === "NotReadableError" || n === "OverconstrainedError") {
                        say("No usable camera. Type the barcode instead.", "warn");
                    } else if (n === "zxing-load") {
                        say("Scanner library didn't load. Type the barcode instead.", "warn");
                    } else {
                        say("Couldn't start the scanner (" + n + "). Type the barcode.", "warn");
                    }
                });
        }
        scanBtn.addEventListener("click", start);

        // on a phone/tablet, drop straight into scanning — that's the whole point of
        // the Scan page there, so don't make them tap first. only on a fresh add form,
        // and not when a ?barcode= is already driving a lookup. if the browser blocks
        // getUserMedia without a tap (older iOS), start()'s own catch just re-shows the
        // Scan button, so nothing's lost.
        var coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
        if (coarse && !qsBarcode && form.getAttribute("data-autoscan")) {
            start();
        }

        cancel.addEventListener("click", function () {
            stop();
            say("");
        });
    }
})();
