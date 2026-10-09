# InvAi - Master Architecture & Business Blueprint

Part A describes the backend as it is built today. Part B is the product blueprint: the business model and
the AI and automation features still to come. Where the blueprint's original design changed during the build, it
says so. Setup is in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md); known gaps are in [TODO.md](TODO.md).

# Part A: What's built

## 1. Technology stack

| Layer | Built | Planned |
|---|---|---|
| API | FastAPI (Python 3.10+), SQLModel / SQLAlchemy, Pydantic | |
| Database | PostgreSQL 17+, schema managed by Alembic migrations | Row-level security |
| Auth | Username + password (bcrypt), JWT bearer tokens valid for 1 hour | Refresh tokens, revocation |
| Frontend | | Next.js (React) with Tailwind CSS |
| AI/ML | | XGBoost / Polars demand sensing; multimodal LLM Co-Pilot, voice logging, vision OCR |
| Feature flags | | LaunchDarkly, for tier-based unlocking |

**Environments.** Development runs against a **local PostgreSQL** server: each developer creates their own `invai`
database, and `scripts/reset_db.py` (which only runs against `localhost`) rebuilds it from the migrations. The
project's earlier hosted database on **Neon** predates Alembic and still has to be brought onto it (see
[TODO.md](TODO.md)). Settings come from `.env` (`DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS`, `DOCS_ENABLED`).

**Code layout.** `app/routers/` holds thin HTTP endpoints, and `app/services/` holds the business logic, which
doesn't import FastAPI, so scripts can call it too. `app/models.py` holds the tables. See
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#layout).

## 2. Tenancy, users and roles

* **Multi-tenant on one database.** Every business is a `BusinessProfile` with a random 8-character ID (e.g. `K7QM2XRA`). Every
  business-owned row (users, products, batches, sales, suppliers, purchase orders, ledger entries) carries a
  `business_id` foreign key, and every query filters on the `business_id` in the caller's token.
* **Roles:** `Owner`, `Manager`, `Staff`. `POST /onboard-business/` creates a business and its Owner, and the Owner
  adds employees.
  * **Staff** can sell, list products, see alerts and batches, mark purchase orders delivered, and see their own sales.
  * **Owner and Manager** can also create and edit products, place, cancel and stock purchase orders, add
    suppliers, run manual audits and the daily check, and see every sale, the ledger, the reports and the
    supplier scorecards.
  * **Only the Owner** can add employees and deactivate or reactivate products.

## 3. Stock: batches, not a single count

The original blueprint modelled loose goods as a **parent-child** pair: a 50 kg sack as the parent, with kilogram
amounts deducted as children. That was replaced by **batches** plus **decimal quantities**, which covers loose
goods and expiry tracking with one model:

* **`ProductBatch`**: each arrival of stock is a batch with its own quantity, `received_date`, optional
  `expiry_date` (none means it never expires), and a link to the purchase order it came from, if any.
  Opening stock and audit increases also become batches.
* **`Product.quantity`** is the product's total, kept equal to the sum of its batches.
* **Selling is soonest-expiry-first (FIFO).** Checkout takes from the batch that expires first (batches that never
  expire go last, then oldest received). Expired batches are never sold. `SaleBatchAllocation` records which
  batches each sale drew from.
* **Concurrency.** Checkout, stocking, audits and the daily check lock the product row and then its batch rows
  (`SELECT ... FOR UPDATE`, always in that order), so two simultaneous sales can't sell the same stock.
* **Expiry.** A batch is expired from its expiry date onward, judged by the date in India (Asia/Kolkata). The
  daily check (`scripts/run_daily_check.py` on a schedule, or `POST /system/daily-check`) empties expired batches
  and records them as disposals. `GET /alerts/expiring-soon/` lists what expires within a chosen number of days.
* **Deactivation.** `DELETE /products/{id}` deactivates a product rather than deleting it, so its sales and
  ledger history stay intact. It can be reactivated.

## 4. Quantities, units and money

* **Units:** each product has a `unit`: `piece`, `kg`, `g`, `litre` or `ml`.
* **Quantities are decimals**, `NUMERIC(12,3)` (to the gram or millilitre for kg and litre products). Products sold
  by the `piece` only accept whole numbers. "Sold 1.5 kilos" is a checkout of `1.5` on a `kg` product.
* **Money is `NUMERIC(12,2)`** (INR). Both quantities and money are exact decimals in the database and in
  Python, and are sent as plain JSON numbers.
* **Time:** timestamps are stored as `timestamptz` in UTC and returned with `+00:00`. Daily figures (reports,
  "today" for expiry) use the India date.

## 5. The stock ledger

Every change to stock writes a `StockMovement` row: the product, batch, change (+/-), the product's total
afterwards, the reason, and who or what caused it (user, sale, purchase order, note).

| Reason | Written by |
|---|---|
| `opening` | Creating a product with opening stock |
| `purchase_receipt` | Stocking a delivered purchase order |
| `sale` | Checkout (one entry per batch drawn from) |
| `audit_increase` / `audit_decrease` | Manual stock count (`PUT /products/{id}/manual-audit`) |
| `expiry_disposal` | The daily check |

* **Append-only, enforced by the database:** Postgres triggers reject any `UPDATE`, `DELETE` or `TRUNCATE` on
  `stock_movement`. Corrections are new entries, never edits.
* **Invariant:** for every product, `Product.quantity` = the sum of its batches = the sum of its ledger entries.
  The test suite checks it throughout, and the daily check reports any product where it fails.
* `GET /inventory/movements` lets an Owner or Manager filter the ledger.

## 6. Inflow: suppliers and purchase orders

* **Purchase order lifecycle:** `PENDING` → `DELIVERED` (`PUT .../deliver`: goods arrived, which stops the
  supplier's lead-time clock) → `STOCKED` (`PUT .../stock`: the received and rejected quantities and the expiry
  date are recorded, and only the accepted quantity becomes a batch and a ledger entry). A `PENDING` order can be
  `CANCELLED`. A short delivery still closes the order (no backorders yet).
* **Supplier scorecards** (`GET /suppliers/scorecards`, `GET /suppliers/{id}/scorecard`) are computed from
  purchase orders over a date range: average lead time, on-time rate, fill rate, defect (rejection) rate and
  price volatility. This is the data the planned AI "Smart Sourcing Hub" will compare vendors on.

## 7. Outflow and reporting

* **Checkout** (`POST /checkout/`) sells one product per request, priced at the product's current price.
* **Sales history** (`GET /sales/`): Staff see their own sales, while Owner and Manager see all sales, with
  totals per unit.
* **Reports** (Owner and Manager), each over an India-date range: sales summary with daily trend, top products,
  dead stock, and waste (quantities disposed of at expiry, per product).
* **Low-stock alerts** (`GET /inventory/alerts/low-stock`) compare each product's quantity with its `min_stock_level`.

## 8. Database and migrations

* **Alembic** owns the schema: `migrations/versions/` starts from a baseline (`13385b7b4ede`), followed by the
  `timestamptz` conversion, foreign keys plus the ledger triggers, and `users.full_name`. The app never creates
  or alters tables, and it **refuses to start** if the database isn't at the latest revision.
* The hand-written SQL that evolved the Neon database before Alembic is kept in `migrations/legacy/` for history.
* **Integrity in the database, not just the app:** foreign keys on every relationship (including `business_id`),
  unique usernames and emails, and the append-only ledger triggers.
* Workflow and gotchas: [docs/DATABASE.md](docs/DATABASE.md).

# Part B: Product blueprint (planned)

## 9. The 3-tier business model
* **Starter (Freemium/Low Cost):** 1 Location, Up to 1,000 SKUs.
* **Growth (Standard Sub):** Up to 5 Locations, Up to 10,000 SKUs. Unlocks multi-store transfers.
* **Enterprise (Base + Per-Node):** Flat Base Fee (covers HQ + first 10 stores) + Micro-fee (e.g., ₹299/mo) for every additional store. Unlimited SKUs.

Not built yet: there are no locations, tiers or SKU limits; each business is a single store.

## 10. The AI monetization engine (two-bucket system)
* **Active AI (Conversational Co-Pilot):** Pay-as-you-go Wallet/Top-Up model. Users burn credits per query. When they hit 0, they buy a top-up bundle (e.g., ₹149 for 500 queries).
* **Passive AI (Demand Sensing):** Background processing tied to the subscription tier. Starter = Weekly scans; Growth = Daily scans; Enterprise = Real-time scans.

## 11. Supply chain inflow (ordering and receiving)
* **Smart Sourcing Hub:** AI compares vendors on a scorecard tracking Average Lead Time, Reliability (Fill Rate), Price Volatility, and Defect Rate. *The scorecard itself is built (section 6); the AI comparison is not.*
* **Autonomous Ordering:** AI drafts Purchase Orders.
    * *The Approval Gate:* Owner gets a push notification to review and click "Send Order" before money is spent (unless bypassed via "Full Auto-Pilot" settings).
    * *Routing:* Orders are automatically sent via Meta WhatsApp API, Email (with CSV), or direct EDI based on vendor tech level.
* **Reconciliation:** When receiving goods, the system compares scanned items against the Digital PO to catch short deliveries before docking vendor scores. *Built in part: stocking records received and rejected quantities, which feed the fill and defect rates; there is no scanning yet.*

## 12. Supply chain outflow (sales and deduction)
To track the "drain" of inventory without slowing down the merchant:
* **Enterprise:** API Webhooks directly integrated into existing POS billing machines.
* **Small Shop (Vision AI):** Owner snaps a photo of their handwritten bill book; AI converts cursive text to JSON inventory deductions.
* **Small Shop (Voice AI):** "Zero-Touch" microphone button. Owner speaks: *"Sold two milks,"* and NLP updates the database.
* **Loose Goods (Khuli Chize):** Voice AI deducts exact amounts (e.g., *"Sold 1.5 kilos"*). *Replaced the parent-child design: loose goods are products with a `kg`/`g`/`litre`/`ml` unit and decimal quantities, sold from batches like everything else (section 3). Whatever channel captures the sale (webhook, vision, voice) will end in the same checkout.*
