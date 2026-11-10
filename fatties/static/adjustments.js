/* Shared, DOM-free adjustment rules, also exercised by Node's built-in test runner. */
(function (root) {
  "use strict";
  const MACROS = ["carbs_g", "protein_g", "fat_g", "sugar_g"];
  const round = (n) => Math.round(n * 10) / 10;
  function create(base) {
    return { base: { ...base }, scale: 1, removed: false, macros: Object.fromEntries(MACROS.map((key) => [key, base[key]])) };
  }
  function scaled(entry, mealScale = 1) {
    const factor = entry.scale * mealScale;
    const out = { ...entry.base };
    for (const key of MACROS) out[key] = round(entry.macros[key] * factor);
    out.sugar_g = Math.min(out.sugar_g, out.carbs_g);
    const energy = entry.base.kcal + 4 * (entry.macros.carbs_g - entry.base.carbs_g) +
      4 * (entry.macros.protein_g - entry.base.protein_g) + 9 * (entry.macros.fat_g - entry.base.fat_g);
    out.kcal = round(Math.max(0, energy) * factor);
    out.estimated_weight_g = round(entry.base.estimated_weight_g * factor);
    if (Math.abs(factor - 1) > 0.00001) out.portion = `${entry.base.portion} (×${Number(factor.toFixed(4))})`;
    return out;
  }
  function setMacro(entry, key, displayedGrams, mealScale = 1) {
    if (!MACROS.includes(key) || !Number.isFinite(displayedGrams) || displayedGrams < 0) return false;
    entry.macros[key] = displayedGrams / (entry.scale * mealScale);
    // Lowering carbs also lowers sugar visibly; sugar cannot exceed total carbs.
    entry.macros.sugar_g = Math.min(entry.macros.sugar_g, entry.macros.carbs_g);
    return true;
  }
  function reset(entry) {
    entry.scale = 1;
    entry.removed = false;
    entry.macros = Object.fromEntries(MACROS.map((key) => [key, entry.base[key]]));
  }
  const api = { create, scaled, setMacro, reset };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.FattiesAdjustments = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
