# Annotation R: describing text and evidence (guideline v1.5, 2026-10-20) — English translation

English translation (2026-10-28) of `R_evidence_zh.md`, the guideline the annotators actually used. The Chinese
version is authoritative; the translation keeps its structure and examples. Labels in the released files use the
English names given in brackets.

For Eden (R) and the Taiwanese student who re-annotated a sample (R2). The two annotate separately, do not
discuss the items, and do not see each other's answers.

## What this annotation is for
Sustainability reports carry many photographs. We want to know three things: which text in the report describes
the photo; whether the photo shows what that text talks about; and whether the photo contains cues that identify
"this firm, this event". Your answers are the reference against which the program is scored, so no program
output is shown while you annotate.

## The screen
- The whole page as an image. The blue box is the photo to annotate.
- Every text block on the page has a grey box drawn in advance (cut from the PDF itself by the program); click
  to select. Grey boxes can be wrong, see the last point of step 1.

## Before you start (R2 only)
- Do 20 practice items first. They are development-split photos that Eden has annotated and do not overlap with
  the real items. After each answer you see Eden's answer. There is no pass mark; the point is to understand the
  rules. Ask Eden about anything unclear.
- Real items come in batches of 50. Hand in a batch when it is done; the next batch opens after a check.

## Steps

### 0. Is the blue box a photo?
If it is not a photo (a chart, an illustration, a logo, a scanned document or certificate, a screenshot, and so
on), tick "Not a photo" (不是照片) and submit; the system supplies the next item. The criterion is the same as in
annotation A: only a real photograph taken with a camera counts.

### 1. Select the text that describes this photo
Click every text box that talks about "what this photo shows".
- Captions, notes beside the photo, and paragraphs or headings that mention the photo's content can all be
  selected.
- Select a heading only if it names a concrete activity, object, place or person, for example "2023 beach
  clean-up" or "Tainan plant solar installation". Headings that only name a broad topic ("Environmental
  sustainability", "Social inclusion") are not selected.
- If one passage describes several photos, select it for each of them.
- Do not select paragraphs about something else, even if they are right next to the photo.
- Look only at this page. If nothing on the page describes the photo, tick "No describing text on this page"
  (這頁沒有描述文字).
- If the describing text is on the page but its grey box is wrong (no box, merged with another passage, or cut
  into several boxes), tick "Describing-text box problem" (描述文字的框有問題). Still select the parts that do
  have boxes; if nothing is boxed, tick this item only.

### 2. The three levels (choose one)
Compare the photo with the text that describes it (including a passage that step 1 could not box). If you ticked
"No describing text on this page", compare the photo with the topic of the section the page belongs to; then
only "topical" or "unrelated" can be chosen.
- No describing text, but the photo fits the section topic → topical (主題相關). Example: a chapter on employee
  care with a photo of colleagues chatting.
- Not even the section topic fits → unrelated (無關). Example: a chapter on information security with a
  landscape photo.
- "No text proves what this photo is" does not mean unrelated: that is exactly "packaging" (a photo decorating the
  section topic), which is topical.

Decide in this order:
1. Does the text mention a **concrete, photographable thing** (an event, an object or piece of equipment, a place,
   a person)? Text with only numbers, ratios, systems or policies (a headcount table, a carbon target) → at most
   "topical".
2. Does the photo show **the kind of thing the text talks about**? Yes → "direct" (直接對應). Whether it is the
   same occasion, the same machine or the same person does not matter here.
3. Not that kind of thing, but the same topic → "topical"; not even the same topic → "unrelated".

**What "topic" means**: the sustainability issue the text (or the section the photo sits in) is about, something
that can be said in a short phrase, for example "renewable energy", "employee health", "community giving",
"recruitment", "information security".
- The topic is not the firm, not the E/S/G category and not "sustainability": if the topic were that broad, every
  photo would be related and "unrelated" would disappear.
- With no describing text, use the heading of the **smallest section** containing the photo (for example
  "2.6 Information security management", not "2 Corporate governance"); if the page carries only the chapter
  title, use that.
- A symbolic photo (handshake, fist bump, forest, globe) with a matching issue: topical (this is "packaging").
  Example (development split): a section on community engagement with a fist-bump photo.
- A photo of the firm's own business placed in an unrelated section: unrelated. Example (development split): a
  section on information security management with a photo of aircraft maintenance staff.

- **Direct**: the photo shows the kind of thing the text talks about: that kind of activity, that kind of object
  or equipment, that place, that person.
  - Whether it is the same occasion or the same machine need not be settled; whether "this firm, this occasion"
    can be recognised is the business of step 3, anchoring.
  - "The kind" is as specific as the text: if the text says beach clean-up, the photo must show a beach clean-up,
    not any volunteer activity; if the text says energy-storage cabinet, the photo must show an energy-storage
    cabinet, not any energy equipment.
  - If the text lists several things (several lectures, several activities) and the photo shows one of them,
    that is direct.
  - A part is enough: a lecture shown only through its audience, a renovation shown only through the finished
    room.
  - Example: the text says "employee beach clean-up in 2023"; the photo shows people picking up litter on a
    beach.
  - Example: the text says "solar panels installed on the roof of the Tainan plant"; the photo shows solar
    panels on a roof.
  - Example: the chairman's portrait beside "A message from the chairman".
  - Example (development split): the text describes a "junior flight attendant camp" run with a hotel; the photo
    shows flight attendants and children in front of a cabin mock-up. Which session it was cannot be told; it is
    still direct.
  - Example (development split): the text says the firm "exhibited energy-storage cabinets" at an energy fair and
    "staff talked with customers on site"; the photo shows people at the booth explaining to visitors.
  - Example (development split): the text lists several lectures ("energy-storage reading seminar at YunTech",
    and others); the photo shows an audience in a lecture hall.
  - Example (development split): caption "stand-by room, changing room", heading "renovation of the cabin crew
    stand-by area"; the photo shows an empty room with a meeting table.
- **Topical**: same topic, but the photo does not show the kind of thing the text talks about.
  - Example: carbon reduction with a forest photo; "integrity in business" with a handshake; a specific training
    course with a generic office photo.
  - Example: the text says beach clean-up; the photo shows tree planting (environmental volunteering too, but not
    a beach clean-up).
  - Example (development split): the text is a headcount table (regular, contract and agency staff); the photo
    shows a maintenance technician at work. A table of figures is not describing text, so nothing is selected in
    step 1; if nothing else on the page is about the photo, tick "No describing text on this page".
- **Unrelated**: not even the topic is related.
  - Example: an abstract image on the cover; a landscape that has nothing to do with the content.

### 3. Anchoring (tick all that apply; look only at the photo itself)
- **Text in the photo that identifies the firm or the event** (照片裡有能認出公司或活動的字): the firm's name
  (full, short or English), an event name, a year (Gregorian or ROC calendar), on a banner, a sign, a backdrop, a
  uniform, a product, a shopfront or a vehicle.
- **The firm's logo in the photo** (照片裡有這家公司的 logo); the group's or a subsidiary's logo counts.
- Tick whenever it is visible and recognisable, regardless of whether it matches the text (matching is the
  program's job).
- If the text or logo was added in post-production and was not at the scene (a title added by the designer, a
  logo in the corner, a watermark): do not tick the two items above; tick "Overlay text or logo" (後製疊上的字或
  logo) instead.
- A recognisable face is not an anchor; only text and logos count.
- Text too small or too blurred to read: do not tick.

### You do not judge "is this an illustration"
- "Illustration" is composed by the program (direct + no anchor). You do not judge it and need not write
  "illustration" in the notes.
- You do two things only: in step 2 choose the level by the text; in step 3 tick only the text or logos **visible
  in the photo**.
- If you cannot tell whether the photo is the firm's own (no firm name, event name, year or logo): choose the
  level as usual and tick no anchor. The program will count it as "illustration", which is exactly what "it is not
  made clear whether this is the firm's own" means.
- Stock photos and staged studio shots (obviously bought or posed) are judged by the same order as in step 2 and
  are **not unrelated just because they are stock**: concrete text and a photo of that kind → direct; text about
  a policy or a topic only and a photo that fits the topic → topical; topic does not fit → unrelated. (Added
  2026-10-20; the rule itself is unchanged.)

### Other ticks
- **Photo box problem** (照片框有問題): the blue box covers only part of the photo, covers two photos, or is
  skewed. Annotate as usual.
- **Not sure** (不確定): tick for items you hesitate over; the reason can go in the notes.

## How the answers are used (not needed while annotating)
The program combines the three levels with anchoring into four levels: direct + anchored = evidence; direct + not
anchored = illustration; topical = packaging; unrelated = unrelated. The text boxes you selected are the reference
for photo-to-text linking; "Describing-text box problem" measures how often the program cuts text boxes wrongly.

## Rules
- Look at no program output and do not turn to other pages of the report.
- About half a minute per item.
- You may correct a slip, but do not go back and change your earlier criteria because of later items.
- Stop after the first 20 items. Put any rule you were unsure of into the guideline (bump the version), then
  continue.

## Version history
- v1 (2026-10-18): first version.
- v1.1 (2026-10-18): practice items and batches for R2.
- v1.2 (2026-10-18): "Describing-text box problem" added; the former "box problem" renamed "Photo box problem".
- v1.3 (2026-10-19): "You do not judge 'is this an illustration'" added (Eden had written "illustration" in the
  notes of the 20 draft items; agreed on 10/19 to put it in the guideline).
- v1.4 (2026-10-19): the decision order in step 2; direct is about "the kind of thing the text talks about", and
  whether it is the same occasion is left to anchoring; text with only figures or policies is at most topical;
  development-split examples added (Eden, 10/19: topical was hard to define, decided to follow the guideline).
  The 8 development photos labelled topical were re-read by Eden under v1.4. Later on 10/19 the example for "no
  describing text" in step 2 was added (rule unchanged; Eden confirmed that the comparison is with the section
  topic); the 12 development photos without describing text were re-read by Eden.
- v1.5 (2026-10-20): "What 'topic' means" added to step 2: a sustainability issue that can be said in a short
  phrase, not the firm and not the E/S/G category; with no describing text, the heading of the smallest section;
  symbolic photos are topical, photos of the firm's own business in an unrelated section are unrelated (after
  Eden discussed 10 development photos with Claude on 10/20).
