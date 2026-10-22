// Cart domain logic for ShopLite. Pure functions over a plain cart object, so
// the same module runs in the browser and in Node's test runner.
// All money values are integer cents to avoid floating point drift.

export const PRODUCTS = [
  { id: "tee", name: "Classic Tee", price: 1999, emoji: "👕" },
  { id: "mug", name: "Dev Mug", price: 1250, emoji: "☕" },
  { id: "cap", name: "Logo Cap", price: 2400, emoji: "🧢" },
  { id: "stickers", name: "Sticker Pack", price: 499, emoji: "✨" },
];

export const COUPONS = {
  SAVE10: { kind: "percent", value: 10, label: "10% off" },
  FREESHIP: { kind: "shipping", label: "Free shipping" },
};

export const SHIPPING_FLAT = 499;
export const FREE_SHIPPING_THRESHOLD = 5000;

export function createCart() {
  // `subtotal` is kept as a running total so the UI can read it cheaply.
  return { items: [], coupons: [], subtotal: 0 };
}

export function findProduct(productId) {
  const product = PRODUCTS.find((p) => p.id === productId);
  if (!product) throw new Error(`Unknown product: ${productId}`);
  return product;
}

export function addItem(cart, productId, quantity = 1) {
  const product = findProduct(productId);
  const line = cart.items.find((item) => item.id === productId);
  if (line) {
    line.quantity += quantity;
  } else {
    cart.items.push({ id: product.id, name: product.name, price: product.price, quantity });
  }
  cart.subtotal += product.price * quantity;
  return cart;
}

export function setQuantity(cart, productId, quantity) {
  const line = cart.items.find((item) => item.id === productId);
  if (!line) return cart;
  cart.subtotal += line.price * (quantity - line.quantity);
  line.quantity = quantity;
  return cart;
}

export function removeItem(cart, productId) {
  cart.items = cart.items.filter((item) => item.id !== productId);
  return cart;
}

export function applyCoupon(cart, rawCode) {
  const code = String(rawCode || "").trim().toUpperCase();
  if (!COUPONS[code]) {
    return { ok: false, message: `"${rawCode}" is not a valid coupon code.` };
  }
  cart.coupons.push(code);
  return { ok: true, message: `Coupon ${code} applied: ${COUPONS[code].label}.` };
}

export function totals(cart) {
  const subtotal = cart.subtotal;

  let discount = 0;
  let freeShipping = subtotal >= FREE_SHIPPING_THRESHOLD;
  for (const code of cart.coupons) {
    const coupon = COUPONS[code];
    if (coupon.kind === "percent") discount += Math.round((subtotal * coupon.value) / 100);
    if (coupon.kind === "shipping") freeShipping = true;
  }

  const shipping = cart.items.length === 0 || freeShipping ? 0 : SHIPPING_FLAT;
  const total = Math.max(0, subtotal - discount + shipping);
  return { subtotal, discount, shipping, total };
}

export function formatMoney(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}
