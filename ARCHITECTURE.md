# InvAi - Master Architecture & Business Blueprint

## 1. Core Technology Stack
* **Frontend:** Next.js (React) with Tailwind CSS.
* **Backend:** FastAPI (Python) for rapid, asynchronous API handling.
* **Database:** PostgreSQL (Relational DB for users, inventory, and transaction logs).
* **AI/ML Engine:** * XGBoost / Polars (For backend Demand Sensing and data crunching).
    * Multimodal LLM (For Co-Pilot, Voice-Logging, and Vision OCR).
* **Feature Flags:** LaunchDarkly (For dynamic tier-based UI unlocking).

## 2. The 3-Tier Business Model
* **Starter (Freemium/Low Cost):** 1 Location, Up to 1,000 SKUs.
* **Growth (Standard Sub):** Up to 5 Locations, Up to 10,000 SKUs. Unlocks multi-store transfers.
* **Enterprise (Base + Per-Node):** Flat Base Fee (covers HQ + first 10 stores) + Micro-fee (e.g., ₹299/mo) for every additional store. Unlimited SKUs.

## 3. The AI Monetization Engine (Two-Bucket System)
* **Active AI (Conversational Co-Pilot):** Pay-as-you-go Wallet/Top-Up model. Users burn credits per query. When they hit 0, they buy a top-up bundle (e.g., ₹149 for 500 queries).
* **Passive AI (Demand Sensing):** Background processing tied to the subscription tier. Starter = Weekly scans; Growth = Daily scans; Enterprise = Real-time scans.

## 4. Supply Chain Inflow (Ordering & Receiving)
* **Smart Sourcing Hub:** AI compares vendors on a scorecard tracking: Average Lead Time, Reliability (Fill Rate), Price Volatility, and Defect Rate.
* **Autonomous Ordering:** AI drafts Purchase Orders. 
    * *The Approval Gate:* Owner gets a push notification to review and click "Send Order" before money is spent (unless bypassed via "Full Auto-Pilot" settings).
    * *Routing:* Orders are automatically sent via Meta WhatsApp API, Email (with CSV), or direct EDI based on vendor tech level.
* **Reconciliation:** When receiving goods, the system compares scanned items vs. the Digital PO to catch short deliveries before docking vendor scores.

## 5. Supply Chain Outflow (Sales & Deduction)
To track the "drain" of inventory without slowing down the merchant:
* **Enterprise:** API Webhooks directly integrated into existing POS billing machines.
* **Small Shop (Vision AI):** Owner snaps a photo of their handwritten bill book; AI converts cursive text to JSON inventory deductions.
* **Small Shop (Voice AI):** "Zero-Touch" microphone button. Owner speaks: *"Sold two milks,"* and NLP updates the database.
* **Loose Goods (Khuli Chize):** Parent-Child database logic. A 50kg sugar sack is the Parent; Voice AI deducts exact Kilogram child amounts (e.g., *"Sold 1.5 kilos"*).