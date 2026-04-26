# InvAi ⚙️: Autonomous AI Supply Chain Platform

> An enterprise-grade Agentic AI SaaS moving beyond conversational interfaces to execute autonomous, data-driven supply chain orchestration.

InvAi is a multi-tenant web platform designed to democratize advanced supply chain analytics. It bridges the gap between sophisticated ML demand sensing and Generative AI, utilizing a custom-trained LLM Co-Pilot and autonomous AI agents to manage inventory, forecast stockouts, and trigger vendor reordering workflows.

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

#### 🤝 Let's Connect

<img src="https://readme-typing-svg.herokuapp.com/?font=Fira+Code&weight=700&size=25&pause=1000&color=39D353&width=800&repeat=false&lines=Architected+%26+Developed+by+Shubham+Yawalkar" alt="Architected & Developed by Shubham Yawalkar" />

<table>
  <tr>
    <td valign="center">
      <a href="https://www.linkedin.com/in/shubham-yawalkar/">
        <img src="https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white" />
      </a>
    </td>
    <td valign="center">
      <b>Let's connect! Click to visit my profile.</b>
    </td>
  </tr>
</table>

$$\large \mathbf{\color{#FF0000}{OPEN\ TO\ AI\ \&\ DS\ INTERNSHIPS}}$$



