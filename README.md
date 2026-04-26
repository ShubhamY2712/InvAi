# InvAi ⚙️: Autonomous AI Supply Chain Platform

> An enterprise-grade Agentic AI SaaS moving beyond conversational interfaces to execute autonomous, data-driven supply chain orchestration.

InvAi is a multi-tenant web platform designed to democratize advanced supply chain analytics. It bridges the gap between sophisticated ML demand sensing and Generative AI, utilizing a custom-trained LLM Co-Pilot and autonomous AI agents to manage inventory, forecast stockouts, and trigger vendor reordering workflows.

## 🚀 Current Status
- **Version 1:** Core FastAPI backend, Next.js interface, and primary ML demand sensing (XGBoost/Prophet) active.
- **Version 2:** Active development. Integrating LangGraph for autonomous agentic actions and fine-tuning Llama 3 via LoRA for localized inventory intelligence.

## 🏗️ Core Architecture & Capabilities

* **Agentic Orchestration:** Utilizes **LangGraph** and **LangChain** to create autonomous AI agents capable of multi-step reasoning, such as detecting stock anomalies and drafting automated supplier restock emails.
* **Fine-Tuned Generative AI:** Powered by **Llama 3 (8B)**, fine-tuned using LoRA to understand specific inventory logic and integrated with **pgvector/Pinecone** for real-time Retrieval-Augmented Generation (RAG).
* **Predictive Demand Sensing:** Processes massive datasets using **Polars** and leverages **XGBoost** and **Prophet** for highly accurate time-series forecasting and scenario planning.
* **Enterprise Multi-Tenancy:** Secure data isolation achieved via **PostgreSQL Row-Level Security (RLS)**, with dynamic feature flagging managed by LaunchDarkly/PostHog.

## 💻 Comprehensive Tech Stack

### AI & Machine Learning
* **Generative & Agentic AI:** Llama 3 (LoRA Fine-Tuned), LangChain, LangGraph, vLLM / Ollama
* **Predictive Data Science:** Polars, XGBoost, Prophet, MLflow, Scikit-learn
* **Vector Storage:** Pinecone / pgvector

### Backend & Infrastructure
* **Core API:** FastAPI (Python 3.x)
* **Databases & Caching:** PostgreSQL (RLS), Redis
* **Task Queues:** Celery, RabbitMQ
* **DevOps & Cloud:** Docker, Kubernetes, AWS / GCP

### Frontend Web & Mobile
* **Framework:** Next.js (React), Zustand
* **Hardware Integration:** HTML5 getUserMedia API (Mobile browser barcode scanning)

🤝 Let's Connect
Architected and developed by Shubham Yawalkar.

LinkedIn Profile: https://www.linkedin.com/in/shubham-yawalkar/

Open to AI Engineering and Data Science internship opportunities.
   ```bash
   git clone [https://github.com/YourUsername/InvAi.git](https://github.com/YourUsername/InvAi.git)
   cd InvAi
