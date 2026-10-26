// Quick-adjust: the restock date's "+3m / +1y" chips, same as the add form.
// Tapping a chip fills the new-expiry field from today; typing a date clears them.
(function () {
    "use strict";

    var dateEl = document.getElementById("restock_date");
    if (!dateEl) { return; }
    var chips = document.querySelectorAll(".chip[data-add]");

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
            dateEl.value = fromToday(chip.getAttribute("data-add"));
            Array.prototype.forEach.call(chips, function (c) { c.classList.remove("is-on"); });
            chip.classList.add("is-on");
        });
    });

    dateEl.addEventListener("input", function () {
        Array.prototype.forEach.call(chips, function (c) { c.classList.remove("is-on"); });
    });
})();
