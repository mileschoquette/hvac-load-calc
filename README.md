# hvac-load-calc

Turns a CAD-exported architectural PDF into an ASHRAE Radiant Time Series (RTS)
cooling and heating load calculation.

The parser reads the drawing's vector geometry to find walls, rooms, windows and
doors, hands them to a browser review UI where you fix anything it got wrong,
then runs the RTS engine over the reviewed rooms and writes a load report with
the supporting evidence attached.

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python3 app.py
```

That opens a local page with two drop zones: your plans PDF, and a JSON file
(the DM-setup types JSON the first time on a project, or a saved
`rooms_edited.json` to pick back up). It sorts out parse-vs-resume for you.

The CLI is there if you want the steps individually:

```bash
python3 loadcalc.py parse "plans.pdf" [types.json] [px_per_ft] [min_len_ft] [page]
python3 loadcalc.py resume rooms_edited.json [types.json]
python3 loadcalc.py calc   rooms_edited.json [types.json]
```

See `HANDOFF.md` for the full workflow and `adicot_loadcalc_parser_architecture.md`
for the design.

## Drawings are not included

No plan sets ship with this repo. Supply your own CAD-exported PDF. Put it
anywhere, or in `sample_drawings/`, which is gitignored along with the per-project
JSON, DXF, and HTML that each run produces.

Note that CAD exporters differ in ways that matter: some write live text, others
convert every label to outlines, in which case `get_text` returns nothing and the
scale and room labels have to be entered by hand. The review UI's **Total ft²**
field recalibrates the scale after the fact, so an approximate px/ft to start is
fine.

## Validation

The engine is checked against the worked examples in ASHRAE Handbook of
Fundamentals Chapter 18. `tests/` reproduces the published per-table values, and
the two golden tests hold the single-room and whole-building totals to within
0.01% and 0.02%.

```bash
for t in tests/test_*.py; do python3 "$t" || echo "FAIL $t"; done
```

The `RTS *.xls` workbooks in the repo root are ASHRAE's own published example
spreadsheets, included because seven of the test files read their expected values
directly out of the sheets. They remain ASHRAE copyright.
