# MARKET_FACTS

_Every number and structural fact extracted from the orientation decks (6 Sep 2026), with
its source slide. **Use these rather than inventing figures** — they are the organisers'
own data and the judges will recognise them._
_Last updated: 2026-09-07._

> **How this was extracted.** Most of these slides are images with no text layer, so
> `pypdf` returned empty pages for roughly half the deck. They were rendered to PNG with
> PyMuPDF at 110 dpi and read visually. If you need a slide not quoted here, render it the
> same way — do not assume an empty text extraction means an empty slide.
>
> Sources, all in the parent folder (read-only):
> `Insurance Business insight Final 1.pdf` (50pp) · `6 Sep Kickoff_KMITL Hackathon.pdf`
> (24pp) · `KMITL_DesignThinking_2026_01.pdf` (21pp) ·
> `KS_Hackathon_Briefing_Insurance in AI Era_VSharing.pdf` (the original brief) ·
> `Krungsri.pdf` (our submitted deck).

---

## 1. The actual challenge statement

Verbatim, and it is **deliberately open** — the Gen Z framing on insight p.35/48 was a
workshop thought-experiment, **not** the brief (confirmed by the user, who was in the room):

> **"ในยุค AI เราจะช่วยให้ Insurance Broker สามารถส่งมอบความคุ้มครองที่ใช่
> ให้กับลูกค้าที่ใช่ ในเวลาที่ใช่ ผ่านช่องทางที่ใช่ ได้อย่างไร?"**

Four "ที่ใช่": **right coverage · right customer · right time · right channel.**
The design deck adds the framing rule, which is a warning worth heeding in the pitch:

> ทั้ง 4 = เรื่องของการ "จับคู่คนกับสิ่งที่เขาต้องการ" … **AI ไม่ใช่จุดเริ่ม**

AI is not the starting point — it is what makes the matching accurate and fast. **Do not
open the pitch with the technology.**

⚠️ **We are already in the 12 finalists on the submitted idea.** The idea is validated;
the job is refinement, evidence and customer fit. Do not pivot.

---

## 2. Logistics — the constraint that shapes everything

| | |
|---|---|
| **Venue** | KMITL Lifelong Learning Center (KLLC) |
| **Day 1 — 12 Sep 2569** | 10:30–12:00 **Mentor Meet-Up, 20 min/team** (Biz / Tech / Design) · 14:00–15:00 Pitching Skill Workshop · 15:00–17:00 work session |
| **Day 2 — 13 Sep 2569** | 10:00–11:00 work · **11:00–12:00 SUBMIT PRESENTATION FILE** · 13:20–14:20 teams 1–6 · 14:30–15:30 teams 7–12 · 16:00 awards |
| **⚠️ PITCH LENGTH** | **5 minutes present + 5 minutes Q&A.** Twelve teams. |
| **Mentor booking** | Opened at orientation, **closes 9 September** |

**The 5-minute limit is the single most important planning fact.** It rules out a live
walkthrough of a multi-step demo. One tight story, one or two screens, and a recorded clip
if anything is shown moving.

### Judging criteria (verbatim, kickoff p.22)

| | |
|---|---|
| **User Insight** | เข้าใจปัญหาที่แท้จริงของลูกค้าและธุรกิจ |
| **Solution & Innovation** | แนวทางแก้ปัญหามีความสร้างสรรค์ ตอบโจทย์ผู้ใช้ และสร้างคุณค่าใหม่ |
| **Feasibility** | เลือกใช้เทคโนโลยีได้อย่างเหมาะสม มีความเป็นไปได้ในการพัฒนาและใช้งานจริง |
| **Business Impact** | มีศักยภาพในการสร้างผลลัพธ์เชิงธุรกิจและยกระดับประสบการณ์ลูกค้า |

### Who is in the room

**Judges:** อ.บิ๊ก อัคเดช (Head of K-DAI, KMITL) · พี่เป้ล กมลวรรณ (Head of Deposit and
Retail Fee Products, Bank of Ayudhya) · พี่โจ้ ประมุข (Head of Innovation, Stellar by
Krungsri).

**Business mentors:** พี่โจอี้ ขวัญชัย (Head of Business Development & Project
Implementation, **Krungsri Auto Broker**) · พี่กิ๊ก วันปิติ (Head of Insurance Department,
Krungsri Consumer) · พี่ท็อป Peerapong (VP, Deposit and Retail Fee Products).

Note the shape of that list: **two of the three business people are motor/consumer broker
operators, and two of the three judges are retail-banking product owners.** An argument
phrased in premium, persistency and product-holding will land better than one phrased in
latency.

---

## 3. THE headline number for the broker turn

**Non-Life distribution channel share (insight p.4), 2012 → 2025:**

| channel | 2012 | 2025 |
|---|---|---|
| **นายหน้า (Broker)** | 55.3% | **75.2%** |
| ช่องทางอื่น (Others) | 16.5% | 12.8% |
| ธนาคาร (Bancassurance) | 14.0% | 8.4% |
| ตัวแทน (Agent) | 14.2% | **3.6%** |

**Broker is three-quarters of non-life distribution and still climbing; the agent channel
has collapsed from 14.2% to 3.6% in thirteen years.** This single chart justifies the whole
broker framing (`D115`) and it is the organisers' own slide.

⚠️ Corrects an assumption from the user's orientation notes: broker is not "one of two main
channels alongside bancassurance" in non-life — it is dominant, and bancassurance is 8.4%.

**By line, 2025 (insight p.5) — broker share:**
Motor Compulsory **84.6%** · Engineering 84.7% · Cargo 85.1% · Motor Voluntary **82.5%** ·
Public Liability 78.8% · Hull 79.0% · IARs 76.2% · Others 69.5% · TA/Travel 63.8% ·
**Health 62.5%** · PA 51.5% · Fire 38.7% (bancassurance 53.2%) · Aviation and Crop are
direct (88.3% / 99.9%).

**Life is the mirror image** (insight p.23): *"ช่องทางตัวแทน และธนาคารยังเป็นแรงขับเคลื่อน
หลักของตลาด โดยช่องทางดิจิทัลมีการเติบโตในอัตราสูงจากฐานที่ยังน้อย."* Agent and bank drive
life; digital grows fast off a small base. This matches the user's note exactly.

---

## 4. Broker vs agent — the official duty split (insight p.30)

Krungsri's own slide, and it confirms `D115` precisely.

| **หน้าที่ของบริษัทประกันภัย** (insurer) | **หน้าที่ของนายหน้าประกันภัย** (broker) |
|---|---|
| รับประกันภัย — underwrite | วิเคราะห์ความต้องการของลูกค้า — analyse needs |
| ดูแลเรื่องความคุ้มครอง — manage coverage | **คัดสรรแบบประกันและบริษัทฯ ที่ตรงตามความต้องการ** — select the plan **and the company** |
| **ชดใช้ค่าสินไหม** — pay claims | บริการด้านกรมธรรม์ — policy service |
| บริการด้านกรมธรรม์ — policy service | **ติดตามการต่ออายุกรมธรรม์** — chase renewals |
| | สร้างความสัมพันธ์กับลูกค้า — build the relationship |

**Claims adjudication is the insurer's, not the broker's** — so a claims call is a *handoff*
for us, which is the taxonomy change Track A makes. And "select the plan **and the company**"
is the literal mandate for compare-and-best-fit.

**Intermediary counts, 1 Jan 2026 (insight p.31):** ตัวแทนประกันภัย 223,278 (91% life) ·
นายหน้าบุคคลธรรมดา 130,187 (60% non-life / 40% life) · corporate brokers and agency offices
in the hundreds.

---

## 5. Market size and growth

**Non-life total direct premium (insight p.3, OIC/NESDC):**

| year | premium (M฿) | growth |
|---|---|---|
| 2012 | 179,596 | +29.8% |
| 2019 | 244,055 | +5.2% |
| 2023 | 284,944 | +3.9% |
| 2024 | 286,557 | +0.6% |
| 2025 | 292,785 | +2.2% |
| 2026f | 301,000–303,900 | +2.5–3.5% |

**The market has stopped growing quickly** — from double digits pre-2013 to 0.6–3.5% now.
Growth has to come from **better matching and retention, not more volume**, which is exactly
the challenge statement's four "ที่ใช่".

**Non-life portfolio by line, 2025 (insight p.8).** Motor 56% (163,600 M฿) / non-motor 44%
(129,185 M฿):
Motor Voluntary 142,952 M (48.8%) · Personal Accident 31,771 M (10.9%) · IARs Property
30,315 M (10.4%) · Motor Compulsory 20,648 M (7.1%) · **Health 19,419 M (6.6%)** · Other
17,538 M · Fire 11,120 M · Cargo 5,071 M · All Risk 4,868 M · Liability Misc 4,549 M ·
**Travel 3,163 M (1.1%)** · Hull 675 M · Liability Marine 695 M.

**H1 2026 vs H1 2025 (insight p.12):** total 151,327 M (+3.4%) · **Motor 84,082 (+3.8%)**
— voluntary 72,666 (+3.2%), compulsory 11,416 (+7.8%) · PA 16,715 (+1.9%) ·
**Health 10,626 (+4.0%)** · Fire & IAR 19,198 (+114.2%) · Marine 3,401 (−0.5%) ·
**Travel 1,640 (−1.5%)**.

**Life (insight pp.20–25, Thai Life Assurance Association):**
Q2/2569 total 341,523 M฿ (+4.57% YoY) · 2568 full year 676,505 M฿ ·
**27.23 million policies** in force (+2.39%) · premium per capita **10,404 ฿/yr** ·
**penetration 3.68% of GDP** · only **37.63% of the population holds a life policy** ·
industry CAR 406% against a 140% supervisory floor.

---

## 6. The two statistics that make the business case

### Renewal is the business, and it leaks (insight p.21)

Life premium Q2/2569 by type:

| | amount (M฿) | share | growth |
|---|---|---|---|
| FYP (first year) | 68,012 | 19.91% | +8.10% |
| SP (single) | 25,728 | 7.53% | −19.54% |
| **RYP (renewal)** | **247,783** | **72.55%** | +6.94% |

**Persistency rate: 84%.** The slide's own words: *เบี้ยต่ออายุ (RYP) ยังเป็นแรงขับเคลื่อน
หลัก … การรักษาอัตราต่ออายุเป็นหัวใจสำคัญของความยั่งยืน.*

**Nearly three-quarters of the money is renewal, one policy in six lapses each year, and
chasing renewals is a named broker duty.** The original brief's journey step 5 leak is
*"ติดต่อ manual ไม่ personalize → lapse"*. That is the same fact stated three ways, and it
is the strongest Business Impact argument available to us.

### Travel: demand up 45%, premium down 1.5%

Thai outbound travel **+45% YoY** in H1 2026 (insight p.17) while travel insurance premium
fell **−1.5%**. Top destinations: Tokyo 12.3%, Singapore 12%, Shanghai 10.1%, Hong Kong
7.5%, Osaka 7.2%, Seoul. A gap that wide is a "right time, right channel" failure, and it
is a one-line pitch statistic.

---

## 7. Product context worth knowing

**Health (insight p.16).** Medical costs rising **8–10% per year**, on a super-aged society.
Focus areas named: products designed per customer segment · flexible cover for specific
risks · SAVE rate for copayment and deductible · *"อายุน้อยเบี้ยถูก สุขภาพดีอนุมัติง่าย"* —
buy young and healthy or risk refusal and loading later. Health + CI riders are **21.81%**
of life premium and growing.

**Life by product (insight p.23), share / growth:** Ordinary 57.68% / +1.26% (Endowment
40.53% / +2.71%, Whole Life 16.73% / **−2.20%**) · Group 6.60% / +7.17%, of which
**Mortgage 4.75% / +9.91%** · Annuity 1.97% / +7.67% · **Investment-Linked 8.05% / +41.55%**
· Health + CI rider 21.81% / +4.52%.

⚠️ **Mortgage-linked life is growing at +9.91% and ILP at +41.55%.** The original brief lists
*"Life-event signal ทางอ้อม — กู้บ้าน, มีบุตร, เปลี่ยนอาชีพ"* as data available to the bank.
A customer taking a Krungsri home loan is a mortgage-protection trigger the bank can already
see. That is the cleanest concrete example of **right customer × right time** in the whole
deck.

**Motor / EV (insight pp.14, 36–47).** 2569 EV premium discount levers: good-driving
history · naming up to 5 drivers · usage-based · continuous-renewal discount · **ADAS**.
Thailand's 30@30 plan is in Phase 3 (2026–2030), targeting 725,000 EV autos produced a year
and **50% of newly registered passenger cars electric by 2030**. EV motor is a new,
fast-growing category where cover genuinely differs between insurers (battery, ADAS), so it
is a strong worked example for comparison — and it is the business mentor's own patch.

---

## 8. The competition, named (insight p.32)

**Thailand Insurance Broker Landscape:** heygoody by เงินติดล้อ · **TQM** · ประกันให้ใจ ·
INSURE X · **rabbit care** · sunday · FairDee · insurverse.

Most are **comparison-site-first**. Our differentiator is not the comparison table — they
already have one. It is that the comparison is **grounded in what the bank already knows
about this specific customer**, and that a human broker arrives holding it. Say this
explicitly in the pitch, because a judge will otherwise ask *"how is this different from
rabbit care?"* — and that is a Q&A question we should want.

**Non-life insurers, large, 2025 (insight p.6), % market share — use these names in the
multi-insurer fixtures:** The Viriyah 14.66% (motor-heavy: 37.65 motor / 5.27 non-motor
BN฿) · Dhipaya 10.71% (non-motor heavy) · Bangkok Insurance 9.91% · Tokio Marine Safety
7.10% · Muang Thai 6.64% · Chubb Samaggi 6.16% · Ergo 4.01% · Thanachart 4.00% ·
**Allianz Ayudhya General 3.52%** · LMG 2.94% · Thaivivat 2.69% · Aioi Bangkok 2.43% ·
AXA 2.25% · The Deves 2.21% · Mitsui Sumitomo 2.07% · MSIG 1.87% · Indara 1.85%.
Top 5 = 49.02%, top 10 = 69.65%, 18 companies = 86.87%.

**Life insurers, Q2/2569 (insight p.25):** AIA 27.13% · FWD 13.81% · Muang Thai Life 10.81%
· Thai Life 10.08% · Krungthai-AXA 7.35% · Prudential 7.06% · **Allianz Ayudhya 6.50%** ·
Bangkok Life 5.29% · Generali 2.40% · Ocean Life 1.73%. Top 5 = 70%, top 10 = 92%.

⚠️ **Allianz Ayudhya appears in both lists and is the Krungsri-affiliated carrier.** A
broker demo in which the affiliated insurer is simply one row among several — and sometimes
*not* the recommended one — is far more credible than one where it always wins, and it is
the honest reading of a broker's duty to the customer. Worth doing deliberately.

**Competitor app/LINE features (insight p.28)** — the table stakes our customer app is
measured against: ดูกรมธรรม์ · ยื่นเคลมออนไลน์ · ชำระเบี้ยออนไลน์ · ดูข้อมูลการลงทุน ·
สถานะ · แจ้งสิทธิลดหย่อน · แก้ไขข้อมูลส่วนตัว · กู้กรมธรรม์ · เอกสารลดหย่อนภาษี · แคมเปญ ·
โปรโมชั่น · สิทธิพิเศษวันเกิด · กรมธรรม์ในครอบครัว · บทความ · ค้นหาโรงพยาบาลใกล้ฉัน ·
Telemedicine · Hotline. Several run gamified wellness tiers (Bronze → Platinum, step
challenges, points).

---

## 9. The design-thinking frame the organisers taught

Worth matching in the deck's structure, because the judges sat through the same workshop.

**Problem statement shape:** Background → Problem → Impact → Objective.

**Persona rule:** *"เจาะจงจนเห็นหน้า"* — specific enough to picture the face. Their bad
example is "a student who wants to exercise"; their good one names the person, year, dorm,
schedule and the exact friction.

**Their worked example (design p.13)** — a thought experiment, not our brief, but it shows
the expected shape: *"เฟิร์ส", 23, six months into his first job, has group cover but does
not know if it is enough, car payments plus a student loan, afraid of being pressured.*
Background: new job + new debt = risk profile changed and he has not noticed. Problem: has
group cover but does not know if it is enough, and the broker does not know when to
approach. Impact: cover that does not match real liabilities; broker approaches the wrong
person at the wrong time. Objective: help the broker deliver the right cover to the right
customer at the right time.

Note how exactly that objective restates the challenge statement. **Our own persona should
end in the same sentence.**
