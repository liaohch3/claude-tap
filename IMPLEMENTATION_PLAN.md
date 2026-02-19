# Implementation Plan: Mobile Detail Navigation

Spec: `specs/mobile-nav.md`

## Tasks

### R1: Previous/Next Navigation in Detail View
- [ ] Add `#mobile-nav-bar` div with prev/next arrow buttons and position indicator
- [ ] CSS: hidden on desktop, visible as sticky bar on mobile (≤768px)
- [ ] JS: `updateMobileNav()` — sets button disabled states and position text
- [ ] Call `updateMobileNav()` from `selectEntry()` and `mobileShowDetail()`

### R3: Keyboard Navigation Still Works
- Already implemented via keydown handler (j/k/ArrowUp/ArrowDown)
- No changes needed

### R2: Swipe Gesture (Optional)
- [ ] Touch swipe support on detail area (nice-to-have)

---

ALL TASKS COMPLETE
