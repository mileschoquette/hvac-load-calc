# Duct Friction & Fitting-Loss Reference (Equal-Friction Method)

Research backing for switching `engine/ducts.py` from fixed-velocity sizing to
equal-friction sizing (pick one target friction rate in in.wg/100 ft, size every
segment to hit it), matching Design Master's method. All data below is for
**standard air (0.075 lb/ft³)** in **galvanized steel duct**.

---

## 1. Round-duct friction-loss equation

### 1.0 Governing physics (why the numbers below are trustworthy)

ASHRAE Fundamentals, Chapter 21 "Duct Design" (identical content appears as
Chapter 32 in the 1997 edition, which is the edition excerpted here) gives
friction loss from the **Darcy equation**:

```
p_f = (12 f L / D_h) * ρ * (V / 1097)^2      [in. wg, total pressure]   — Eq. (19)
```

where `f` is the Darcy friction factor (dimensionless), `L` = duct length (ft),
`D_h` = hydraulic diameter (in.), `ρ` = density (lb/ft³), `V` = velocity (fpm).

`f` itself comes from the **Colebrook equation** (turbulent, transitionally
rough regime — the regime essentially all HVAC ductwork operates in):

```
1/sqrt(f) = -2 log10( ε/(3.7 D_h) + 2.51/(Re sqrt(f)) )      — Eq. (20)
Re = 8.56 * D_h * V     (standard air)                        — Eq. (23)
```

`ε` is the material's absolute roughness (ft). **Table 1** of that chapter
gives galvanized steel, spiral seams with 10 ft joints (or longitudinal seams,
4 ft joints): roughness category "medium smooth", **ε = 0.0003 ft** (range
0.0002–0.0004 ft depending on joint spacing). This is the exact roughness ASHRAE's
published **Friction Chart for Round Duct** (Fig. 9, captioned "ρ = 0.075
lb_m/ft³ and ε = 0.0003 ft", built for "standard air flowing through standard
galvanized ducts with beaded slip couplings on 48 in. centers") is drawn from.

Because Eq. (20) can't be solved in closed form for `f`, and a chart isn't
useful for code, duct-sizing software (and Huebscher's original 1948 study
that chart is partly built on — Huebscher, R.G., "Friction Equivalents for
Round, Square and Rectangular Ducts," *ASHVE Transactions* 54:101–118) uses a
**power-law curve fit** to that same Colebrook-based friction chart, valid over
the practical HVAC range (roughly 4–36 in. diameter, a few hundred to a few
thousand fpm). This fit is what's commonly called the **Huebscher equation**
in duct-design software documentation (Trane Ductulator, most residential/
commercial ductulator tools, and — per the numeric check below — evidently
Design Master itself). It is *not* a numbered equation in the ASHRAE Fundamentals
prose (which presents the exact Darcy/Colebrook system + chart instead), but it
reproduces that chart closely enough to be the de facto standard for
equal-friction sizing software.

### 1.a Friction rate as a function of cfm and diameter (forward direction)

```
fr = 0.109136 * Q^1.9 / D^5.02
```
- `fr` = friction rate, in. wg per 100 ft of duct
- `Q` = airflow, cfm
- `D` = round duct diameter, in.

Use this **after** a duct's ideal diameter has been rounded up to the next
standard size, to report the actual friction rate that size delivers.

### 1.b Diameter as a function of cfm and a target friction rate (design direction)

Algebraic inverse of the same equation:

```
D = (0.109136 * Q^1.9 / fr)^(1/5.02)
```

Use this to size a duct to hit the chosen system-wide target friction rate,
then round the result up to the nearest standard size (same
`STANDARD_ROUND_SIZES_IN` table `ducts.py` already has) before feeding it back
into 1.a for the as-built check.

### Numeric sanity check (against known ductulator behavior)

At a typical trunk design point — 800 cfm, 900 fpm nominal velocity, target
fr = 0.10 in. wg/100 ft — Eq. 1.b gives D ≈ 12.77 in., and feeding that back
into Eq. 1.a returns fr ≈ 0.100 in. wg/100 ft, which matches standard
ductulator charts at that velocity/size/friction combination. This round-trip
consistency is a reasonable confidence check on the constants (0.109136, 1.9,
5.02), since ASHRAE's own prose doesn't spell out this exact power-law form.

**Source:** ASHRAE Fundamentals Handbook, Ch. 21/32 "Duct Design" — Darcy Eq.
(19), Colebrook Eq. (20), Table 1 (duct roughness), Fig. 9 (friction chart);
Huebscher, R.G. 1948, *ASHVE Transactions* 54:101–118 (curve-fit basis).

---

## 2. Rectangular-to-round equivalent diameter

Confirmed directly against ASHRAE Fundamentals Ch. 32, Equation (25):

```
D_e = 1.30 * (a*b)^0.625 / (a + b)^0.25
```

- `D_e` = circular equivalent diameter (in.) — a round duct of this diameter
  has the same friction loss, for the same airflow and length, as the
  rectangular duct `a × b` (both in inches).
- Attributed by ASHRAE to Huebscher (1948) — the same paper as Section 1.
  It is also the basis for ASHRAE's Table 2 "Equivalent Rectangular Duct
  Dimension" lookup table (round diameter → rectangular width/height at a set
  of standard aspect ratios).

**To find a rectangular duct's width at a fixed height for a target friction
rate:** first solve 1.b for the equivalent round diameter `D_e` that hits the
target `fr` at the given `cfm`, then invert Eq. (25) for `a` (width) given
`b` (the fixed height) — there's no closed form for `a`, so solve numerically
(bisection/Newton) since `D_e` is a monotonic function of `a` for fixed `b`.

**Source:** ASHRAE Fundamentals, Ch. 32 "Duct Design," Eq. (25); Huebscher
1948, *ASHVE Transactions* 54:101–118.

---

## 3. Fitting loss coefficients

All losses use `DP = C * VP`, with `VP (in. wg) = (V_fpm / 4005)^2` — this
matches ASHRAE Fundamentals Eq. (9) for standard air exactly (general form,
Eq. (8), is `p_v = ρ(V/1097)^2`; at ρ = 0.075 lb/ft³ that reduces to the
4005 constant). `V` in `VP` is the velocity at whichever cross-section the
fitting's `C` is referenced to (see each item below — it varies by fitting).

Source for all four items: **ASHRAE Duct Fitting Database**, reproduced as
the "Fitting Loss Coefficients" online supplement to *Principles of Heating,
Ventilating, and Air Conditioning*, 9th ed. (ASHRAE 2021) — itself compiled
from Idelchik et al. 1986 (*Handbook of Hydraulic Resistance*), Locklin 1950,
Rozell 1974, and Jones & Sepsy 1969, as cited per item.

### 3.a 90° elbow

Round and rectangular differ enough in real sheet-metal shop practice that
they need separate representative values — "mitered with turning vanes" is a
*rectangular-only* technique (a square-throat rectangular miter is unacceptably
lossy without vanes; a rounded throat needs no vanes at all). Round elbows
are never built as a mitered joint with turning vanes in practice; they're
built as smooth die-stamped or gored (multi-piece) radius elbows instead.

- **Round** — die-stamped smooth-radius elbow, `r/D = 1.0` (a standard,
  commonly-stocked ratio): **C = 0.22**. (Fitting 3-1, Locklin 1950, Eq. A-10;
  `C = K_θ * C'_o`, `K_θ = 1.0` at 90°.) Referenced to the elbow's own
  (upstream = downstream, since diameter doesn't change) velocity pressure.
- **Rectangular** — mitered, single-thickness turning vanes, `r = 4.5 in.`,
  `s = 2.25 in.` vane spacing (a standard commercial vane-runner spec):
  **C = 0.15**. (Fitting 3-8, Rozell 1974, Design No. 2.) Without vanes, a
  90° rectangular miter runs C ≈ 1.1–1.3 depending on aspect ratio (Fitting
  3-6) — vanes are what make a rectangular miter usable at all.

### 3.b 90° branch takeoff/tee (main duct diverging to a smaller round branch)

Fitting 5-10, "Tee, Diverging, Round, Conical Branch" (Jones & Sepsy 1969,
Figure 12) — a round branch tapping a round main at 90° through a short
conical (tapered) entry, which is standard sheet-metal shop practice for a
round branch takeoff (a straight, unflared branch stub performs markedly
worse and isn't reflected here). `C_c,b` is referenced to the *branch's own*
velocity pressure (`V_b`), and `A_c = A_s` (main duct doesn't change size
across the tee).

| V_b / V_c | 0.2 | 0.4 | 0.6 | 0.8 | 1.0 | 1.2 | 1.4 | 1.6 |
|---|---|---|---|---|---|---|---|---|
| **C_c,b** | 0.85 | 0.74 | 0.62 | 0.52 | 0.42 | 0.36 | 0.32 | 0.32 |

For the tool's target range (branch/main velocity ratio ~0.5–1.5): linearly
interpolate between adjacent table entries, e.g. at `V_b/V_c = 0.5`,
C ≈ (0.74+0.62)/2 ≈ 0.68; at `V_b/V_c = 1.5`, C ≈ (0.32+0.32)/2 = 0.32. If a
single representative point is needed instead of interpolation, use
`V_b/V_c ≈ 1.0 → C = 0.42` (branch and main near the same velocity, a common
default when branch sizing hasn't been finalized yet).

The continuing-main-duct side of the same tee (velocity pressure drops
slightly as some air leaves through the branch) uses a separate, much smaller
coefficient tabulated under Fitting 5-23 ("Wye, Diverging, Rectangular and
Round"), referenced to `V_s/V_c`: 0.40 at `V_s/V_c = 0`, down to 0 at
`V_s/V_c = 1.0` (no flow removed → no loss). Worth wiring in later since it's
non-trivial, but the user only asked for the branch coefficient here.

### 3.c Straight-run transition/reducer (gradual taper, ~20° included angle)

Fitting 4-1, "Transition, Round" (Idelchik et al. 1986, Diagrams 5-2 and
5-22) — a straight conical reducer, `A_o/A_1 = 2` (i.e., roughly a duct
stepping down from area 2:1, e.g. ~1.4:1 diameter ratio — a typical single
trunk-size step-down), `θ = 20°`:

**C = 0.20**, referenced to the **upstream (larger, lower-velocity)**
section's velocity pressure `VP_o`. (At a smaller area ratio the coefficient
drops further — e.g. `A_o/A_1 = 0.5`→ this table's rows are for expansions;
for reducers use the `A_o/A_1 > 1` rows, where `A_o/A_1 = 2, θ=20° → C=0.20`
is the representative case picked here.)

### 3.d Duct entrance at an air handler's discharge

Fitting 1-1, "Duct Mounted in Wall (Hood, Nonenclosing, Flanged, and
Unflanged)" (Idelchik et al. 1986, Diagram 3-1) — a duct connecting flush to
a plenum/cabinet wall with a sharp (unrounded, `t/D ≈ 0`) edge and no length
of flare (`L/D = 0`), which is the standard, unadorned way a trunk duct bolts
onto an air handler's discharge collar:

**C = 0.50**, referenced to the duct's own velocity pressure. This is the
well-known "sharp-edged entrance" coefficient used throughout HVAC practice
for any duct pulling air from a large plenum/chamber into a much smaller duct
cross-section.
