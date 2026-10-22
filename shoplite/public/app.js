// ShopLite UI: renders products and the cart, wiring buttons to cart.js.
import {
  PRODUCTS, addItem, applyCoupon, createCart, formatMoney, removeItem, setQuantity, totals,
} from "./cart.js";

const cart = createCart();
const $ = (id) => document.getElementById(id);

function renderProducts() {
  $("products").innerHTML = PRODUCTS.map((p) => `
    <article class="product" data-testid="product-${p.id}">
      <span class="emoji" aria-hidden="true">${p.emoji}</span>
      <strong>${p.name}</strong>
      <span class="price">${formatMoney(p.price)}</span>
      <button data-add="${p.id}" data-testid="add-${p.id}">Add to cart</button>
    </article>`).join("");
}

function renderCart() {
  $("cart-items").innerHTML = cart.items.map((item) => `
    <li class="cart-item" data-testid="cart-line-${item.id}">
      <strong>${item.name}</strong>
      <button class="ghost" data-remove="${item.id}" data-testid="remove-${item.id}" aria-label="Remove ${item.name}">Remove</button>
      <div class="controls">
        <button class="qty" data-dec="${item.id}" data-testid="dec-${item.id}" aria-label="Decrease quantity">−</button>
        <span data-testid="qty-${item.id}">${item.quantity}</span>
        <button class="qty" data-inc="${item.id}" data-testid="inc-${item.id}" aria-label="Increase quantity">+</button>
      </div>
      <span class="line-total">${formatMoney(item.price * item.quantity)}</span>
    </li>`).join("");

  $("cart-empty").hidden = cart.items.length > 0;
  $("applied-coupons").innerHTML = cart.coupons.map((code) => `<li>${code}</li>`).join("");

  const t = totals(cart);
  $("subtotal").textContent = formatMoney(t.subtotal);
  $("discount").textContent = `-${formatMoney(t.discount)}`;
  $("shipping").textContent = formatMoney(t.shipping);
  $("total").textContent = formatMoney(t.total);
  $("checkout").disabled = cart.items.length === 0;
}

function showMessage(text, ok) {
  const el = $("coupon-message");
  el.textContent = text;
  el.className = `message ${ok ? "ok" : "error"}`;
}

document.addEventListener("click", (event) => {
  const { add, remove, inc, dec } = event.target.dataset;
  if (add) addItem(cart, add);
  else if (remove) removeItem(cart, remove);
  else if (inc || dec) {
    const id = inc || dec;
    const line = cart.items.find((item) => item.id === id);
    setQuantity(cart, id, line.quantity + (inc ? 1 : -1));
  } else return;
  renderCart();
});

$("coupon-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const result = applyCoupon(cart, $("coupon-input").value);
  showMessage(result.message, result.ok);
  if (result.ok) $("coupon-input").value = "";
  renderCart();
});

$("checkout").addEventListener("click", () => {
  alert(`Order placed! You paid ${formatMoney(totals(cart).total)}.`);
});

renderProducts();
renderCart();
