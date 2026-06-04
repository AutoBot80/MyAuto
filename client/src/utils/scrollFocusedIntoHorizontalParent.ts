/** Keep a focused control visible inside a horizontal scroll container (e.g. Add Sales three-col). */

const FOCUS_SCROLL_PADDING_PX = 8;
const ADD_SALES_SECTION2_BOX = ".add-sales-v2-box-extracted";

export function isHorizontallyScrollableFocusTarget(target: EventTarget | null): target is HTMLElement {
  return (
    target instanceof HTMLElement &&
    target.matches("input, select, textarea, button, [tabindex]:not([tabindex='-1'])")
  );
}

export function scrollFocusedIntoHorizontalParent(element: HTMLElement, scrollParent: HTMLElement): void {
  const parentRect = scrollParent.getBoundingClientRect();
  const elRect = element.getBoundingClientRect();
  if (elRect.left < parentRect.left) {
    scrollParent.scrollLeft -= parentRect.left - elRect.left + FOCUS_SCROLL_PADDING_PX;
  } else if (elRect.right > parentRect.right) {
    scrollParent.scrollLeft += elRect.right - parentRect.right + FOCUS_SCROLL_PADDING_PX;
  }
}

/** Section 2: never scroll the three-col row right on focus (e.g. DOB); revert browser scroll in rAF. */
export function syncAddSalesThreeColFocus(target: HTMLElement, scrollParent: HTMLElement): void {
  if (target.closest(ADD_SALES_SECTION2_BOX)) {
    const scrollLeftBefore = scrollParent.scrollLeft;
    const parentRect = scrollParent.getBoundingClientRect();
    const elRect = target.getBoundingClientRect();
    if (elRect.left < parentRect.left) {
      scrollParent.scrollLeft -= parentRect.left - elRect.left + FOCUS_SCROLL_PADDING_PX;
    }
    const clampScrollRight = () => {
      if (scrollParent.scrollLeft > scrollLeftBefore) {
        scrollParent.scrollLeft = scrollLeftBefore;
      }
    };
    requestAnimationFrame(() => {
      clampScrollRight();
      requestAnimationFrame(clampScrollRight);
    });
    return;
  }
  scrollFocusedIntoHorizontalParent(target, scrollParent);
}
