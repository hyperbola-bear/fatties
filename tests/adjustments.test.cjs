const { test } = require("node:test");
const assert = require("node:assert/strict");
const A = require("../fatties/static/adjustments.js");
const base = { name: "Chicken", portion: "one thigh", kcal: 177, estimated_weight_g: 100, carbs_g: 2, protein_g: 30, fat_g: 9, sugar_g: 1 };

test("portion changes preserve original energy and proportional macro corrections", () => {
  const item = A.create(base);
  assert.equal(A.scaled(item).kcal, 177);
  A.setMacro(item, "protein_g", 40);
  assert.equal(A.scaled(item).kcal, 217);
  item.scale = 0.5;
  assert.equal(A.scaled(item).protein_g, 20);
  assert.equal(A.scaled(item).kcal, 108.5);
  assert.equal(A.scaled(item, 0.5).protein_g, 10);
  A.reset(item);
  assert.deepEqual(A.scaled(item), base);
});
test("zero estimates are editable, sugar stays within carbs and is not double-counted", () => {
  const item = A.create({ ...base, carbs_g: 0, sugar_g: 0 });
  A.setMacro(item, "carbs_g", 10);
  const energy = A.scaled(item).kcal;
  A.setMacro(item, "sugar_g", 5);
  assert.equal(A.scaled(item).kcal, energy);
  A.setMacro(item, "carbs_g", 2);
  assert.equal(A.scaled(item).sugar_g, 2);
  A.setMacro(item, "sugar_g", 100);
  assert.equal(A.scaled(item).sugar_g, 2);
  assert.equal(A.setMacro(item, "protein_g", Infinity), false);
  assert.equal(A.setMacro(item, "protein_g", -5), false);
});
test("sliding back and forth does not accumulate rounding drift", () => {
  const item = A.create(base);
  A.setMacro(item, "protein_g", 40);
  for (let i = 0; i < 1000; i++) { item.scale = 0.75; A.scaled(item, 1.25); item.scale = 1; A.scaled(item); }
  assert.equal(A.scaled(item).protein_g, 40);
  assert.equal(base.protein_g, 30);
  item.scale = 0.55;
  assert.equal(A.scaled(item).portion, "one thigh (×0.55)");
});
