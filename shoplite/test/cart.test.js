// Existing ShopLite test suite. All of these pass today; Repro adds a new
// regression test for each bug it fixes.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  addItem, applyCoupon, createCart, formatMoney, setQuantity, totals,
  SHIPPING_FLAT,
} from "../public/cart.js";

test("adding an item updates the subtotal", () => {
  const cart = addItem(createCart(), "tee", 2);
  assert.equal(totals(cart).subtotal, 3998);
});

test("adding the same product twice merges the line", () => {
  const cart = createCart();
  addItem(cart, "mug");
  addItem(cart, "mug");
  assert.equal(cart.items.length, 1);
  assert.equal(cart.items[0].quantity, 2);
});

test("increasing quantity updates the subtotal", () => {
  const cart = addItem(createCart(), "cap");
  setQuantity(cart, "cap", 3);
  assert.equal(totals(cart).subtotal, 7200);
});

test("flat shipping applies below the free-shipping threshold", () => {
  const cart = addItem(createCart(), "stickers");
  assert.equal(totals(cart).shipping, SHIPPING_FLAT);
  assert.equal(totals(cart).total, 499 + SHIPPING_FLAT);
});

test("SAVE10 takes 10% off the subtotal", () => {
  const cart = addItem(createCart(), "tee");
  const result = applyCoupon(cart, "save10");
  assert.equal(result.ok, true);
  assert.equal(totals(cart).discount, 200);
});

test("unknown coupon codes are rejected", () => {
  const cart = addItem(createCart(), "tee");
  const result = applyCoupon(cart, "BOGUS");
  assert.equal(result.ok, false);
  assert.equal(cart.coupons.length, 0);
});

test("money is formatted with two decimals", () => {
  assert.equal(formatMoney(1250), "$12.50");
});
