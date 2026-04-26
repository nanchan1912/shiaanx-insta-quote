# ShiaanX — Week Actions (Week of 2 March 2026)

## Context
We are at the start of Month 1 of the 6-month Phase 1 plan. The platform decision has just switched from Autodesk Fusion → **Autodesk PowerMill** (C# plugin) based on confirmed partner shop relationships. The AI engine, cloud API, and ML stack remain unchanged — only the client-side integration layer changes.

---

## Priority 1 — Partner Shop (Do This First)

- [ ] **PowerMill version audit** — contact all confirmed pilot partner shops and document which PowerMill version they are running (2022 / 2023 / 2024 / 2025). This determines which `PowerMill.dll` to generate and sets the compatibility baseline for the entire plugin.
- [ ] **Collect .ptf toolpath templates** — ask the partner shop's senior CNC programmer to export their 10 most commonly used PowerMill toolpath templates as `.ptf` files. These seed the template library that the AI will inject strategies through.
- [ ] **Confirm 10–20 real parts for pilot** — agree on a set of representative prismatic parts (aluminum or mild steel) the shop will share as STEP files for initial testing. Set expectations: Phase 1 targets prismatic parts only.

---

## Priority 2 — Engineering Setup

- [ ] **Set up C# Visual Studio project** — create the PowerMill plugin scaffold in Visual Studio 2022. Generate `PowerMill.dll` from the partner shop's installed PM version using `tlbimp.exe`. Verify COM connection established — plugin appears in PowerMill Plugin Manager with a visible side panel ("Hello World").
- [ ] **Stand up AWS infrastructure** — spin up VPC, ECS/Fargate, RDS (PostgreSQL), and S3 bucket. Deploy FastAPI skeleton with `/health`, `/analyze`, `/generate` stub endpoints. Verify the plugin can POST a ping and receive a response.
- [ ] **Confirm C# / .NET developer** — the plugin requires Windows/.NET/COM experience. Either confirm this is covered on the current team or engage a contractor. This is a Month 1 risk item — do not leave it unresolved past this week.

---

## Priority 3 — AI/ML Foundation

- [ ] **Set up pythonOCC environment** — install and verify pythonOCC locally. Run a test: load one STEP file, extract B-Rep faces and edges, convert to a graph structure. This validates the geometry pipeline before dataset generation begins.
- [ ] **Define feature taxonomy** — with the machining domain expert, define the 12–15 prismatic feature types that the AFR model will classify (pockets, slots, holes, steps, bosses, chamfers, threads, etc.) including key dimensions per feature type. This is the foundation of the labelling schema for the synthetic dataset.
- [ ] **Begin synthetic dataset generation pipeline** — write the pythonOCC script that generates diverse prismatic CAD parts with ground-truth feature labels. Target: 1,000 labeled parts in S3 by end of Month 1.

---

## Priority 4 — Business & Process

- [ ] **Draft pilot pitch** — prepare a one-page pitch for 2–3 enterprise pilot customers (drone / industrial machinery / aerospace tier-2). Lead with: 50% reduction in CNC programming time, full digital traceability, DFM feedback free of cost. Emphasise AS9100-aligned process discipline from day one.
- [ ] **Initiate ISO9001 process** — identify a consultant or framework to begin internal ISO9001 alignment. ShiaanX defines process flow, QA plan, and documentation for all partner-executed jobs. This is table stakes for aerospace and drone sector credibility.
- [ ] **Vendor base — first 3 shops** — identify and begin onboarding conversations with 3 manufacturing vendors covering: (1) 3-axis VMC for aluminum/steel prismatic parts, (2) 5-axis capability for complex geometry, (3) inspection capability (CMM or probing). Prepare MOU/NDA templates.

---

## End-of-Week Gate Check

By end of week, you should be able to answer YES to all of the following:

- Partner shop PowerMill version is known and `PowerMill.dll` is generated
- C# plugin scaffold exists and connects to a running PowerMill session
- AWS API is live with `/health` returning 200
- Feature taxonomy draft exists and has been reviewed by a machinist
- C# developer confirmed (team or contractor)
- At least 1 vendor onboarding conversation initiated

---

## Deferred (Not This Week)

- NX and Mastercam integration — Phase 2
- Fusion plugin — Phase 2 (months 7–10)
- 5-axis strategies — Phase 3
- Collision avoidance, digital twin, closed-loop feedback — Phase 3–4
- ISO9001 certification — ongoing, not blocking pilot work
