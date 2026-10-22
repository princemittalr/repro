# ShopLite

A tiny checkout demo store used to showcase [Repro](https://github.com/princemittalr/repro) fixing real bugs end to end.

> ShopLite contains **deliberate bugs**. Repro watches a screen recording of each one,
> writes a failing Playwright test, fixes the code, and opens a pull request here.

## Run it

```bash
npm start      # http://localhost:3000
npm test       # existing unit tests (all pass)
```

No dependencies: Node.js 20+ only.

## Known bugs (for the demo)

| Bug | How to see it |
|-----|---------------|
| Coupon stacks | Apply `SAVE10` twice: the discount doubles to 20% |
| Quantity goes to zero or below | Press − until the quantity hits 0, -1, ... and the total drops |
| Wrong total after removing an item | Add two products, remove one: the subtotal still counts it |

## License

MIT
