# Presentation image prompt (for Gemini / image generation)

Professional, presentation-grade image concept for showing the Rebotic body
to an AI expert / scientist audience. Hero shot + technical exploded-inset
hybrid — see `body/PROJECT.md` and `parts-list.pdf` for the underlying
hardware facts this prompt draws from.

```
Create a professional, presentation-grade image of a real hand-built expo
robot prototype, composed for a technical/scientific audience (think: a
keynote hardware-reveal slide crossed with a robotics-conference figure).

MAIN IMAGE — HERO SHOT (occupies ~80% of the frame):
A small humanoid desk robot — rounded head, short two-axis neck, boxy torso,
two simple arms, on a small pedestal base — photographed as a genuine
engineering prototype, NOT a glossy consumer toy. Material: matte foam-board/
foam-core panels, visible hand-cut seams and assembly lines, realistic
craft-prototype texture (like a well-made robotics-competition or maker-faire
build) — authenticity matters more than shine here. Two round LED-ring eyes
glow soft cyan with a subtle realistic light bloom/glow. Small visible
mechanical details are allowed and welcome (a servo horn at the shoulder, a
thin wire bundle at the base) — these read as credible engineering, not clutter.

LIGHTING & BACKGROUND: Deep charcoal-to-black studio background with a subtle
gradient. Dramatic single-source key light from upper front-left plus a thin
rim/edge light tracing the robot's silhouette so it separates cleanly from
the dark background (no part of the robot should disappear into shadow). Soft
reflection of the robot faintly visible on a dark glossy floor plane beneath
the pedestal, like a professional product-launch photo.

INSET PANEL (small, bottom-right or bottom-left corner, roughly 20% of the
frame, clearly separated by a thin border or subtle panel background):
A technical exploded/schematic breakdown of the robot's key internals,
rendered in a contrasting blueprint/engineering-diagram style — thin cyan or
white linework on a dark or transparent background, minimal color, precise
and clean, like a figure from an IEEE robotics paper. Show 4-5 components
pulled apart with thin leader lines and short spec-sheet-style labels only
(small caps, monospace or clean sans-serif, no long sentences):
  - "ESP32 — WiFi controller"
  - "SG90 servo x4 — neck & arm motion"
  - "WS2812B — RGB LED eyes"
  - "USB mic + speaker — I/O"
  - "5V rail — dedicated servo power"

TYPOGRAPHY: One small, restrained title plate near a corner (not overlapping
the robot) reading "REBOTIC" in a clean modern sans-serif, with a thin
subtitle underneath in smaller text: "Conversational Expo Robot". No other
text on the main hero image — keep it clean.

STYLE REFERENCES: Cross between an Apple/Tesla-style hardware keynote reveal
photo (for the lighting/background/composition) and a robotics research-paper
component figure (for the inset schematic only). Overall mood: credible,
engineered, quietly impressive — not cartoonish, not overly "cute," no
sci-fi/menacing tone either. No stray decorative icons, logos, or flourishes
anywhere in the frame beyond what's specified above. Landscape orientation,
16:9, presentation-slide ready.
```

## Known fixes carried over from prior critique rounds

- Material must stay matte/hand-built foam-board — models default to glossy
  injection-molded plastic unless pushed hard against it.
- Keep inset component icons visually distinct from each other (no duplicate-
  looking circular grille icons for different parts).
- No stray decorative flourishes/sparkles outside the specified composition.
