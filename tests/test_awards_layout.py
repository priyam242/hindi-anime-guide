import sys
import os
import time
import threading
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from playwright.sync_api import sync_playwright

def run_test():
    from app.main import app
    
    server_port = 8899
    config = uvicorn.Config(app=app, host="127.0.0.1", port=server_port, log_level="warning")
    server = uvicorn.Server(config)
    
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    
    # Wait for server to start
    time.sleep(2)
    
    url = f"http://127.0.0.1:{server_port}/awards"
    viewports = [
        {"name": "Mobile (360px)", "width": 360, "height": 800},
        {"name": "Tablet (768px)", "width": 768, "height": 1024},
        {"name": "Desktop (1280px)", "width": 1280, "height": 800},
    ]
    
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for theme in ["light", "dark"]:
            for vp in viewports:
                context = browser.new_context(viewport={"width": vp["width"], "height": vp["height"]})
                page = context.new_page()
                
                # Navigate to awards page
                page.goto(url, wait_until="networkidle")
                
                # Apply theme
                if theme == "dark":
                    page.evaluate("document.documentElement.classList.add('dark')")
                else:
                    page.evaluate("document.documentElement.classList.remove('dark')")
                page.wait_for_timeout(300)
                
                # 1. Check document.scrollWidth <= window.innerWidth
                scroll_check = page.evaluate("""() => {
                    const scrollWidth = document.documentElement.scrollWidth;
                    const innerWidth = window.innerWidth;
                    return {
                        scrollWidth,
                        innerWidth,
                        noHorizontalScroll: scrollWidth <= innerWidth
                    };
                }""")
                
                # 2. Check no child's right edge exceeds its card's right edge
                card_overflow_check = page.evaluate("""() => {
                    const cards = Array.from(document.querySelectorAll('.awards-category-card'));
                    const overflows = [];
                    cards.forEach((card, idx) => {
                        const cardRect = card.getBoundingClientRect();
                        const children = Array.from(card.querySelectorAll('*'));
                        children.forEach((child) => {
                            const childRect = child.getBoundingClientRect();
                            // allow 1.5px subpixel tolerance
                            if (childRect.right > cardRect.right + 1.5 && childRect.width > 0 && childRect.height > 0) {
                                overflows.push({
                                    cardIdx: idx,
                                    tagName: child.tagName,
                                    className: child.className,
                                    childRight: childRect.right,
                                    cardRight: cardRect.right,
                                    diff: childRect.right - cardRect.right
                                });
                            }
                        });
                    });
                    return {
                        totalCards: cards.length,
                        overflowCount: overflows.length,
                        overflows: overflows.slice(0, 5)
                    };
                }""")
                
                # 3. Check nominee position directly under winner
                gap_check = page.evaluate("""() => {
                    const cards = Array.from(document.querySelectorAll('.awards-category-card'));
                    const gaps = [];
                    cards.forEach((card, idx) => {
                        const winnerBlock = card.querySelector('.bg-gradient-to-b');
                        const nomineesBlock = card.querySelector('[x-data]');
                        if (winnerBlock && nomineesBlock) {
                            const wRect = winnerBlock.getBoundingClientRect();
                            const nRect = nomineesBlock.getBoundingClientRect();
                            // gap between bottom of winner and top of nominees
                            const gap = nRect.top - wRect.bottom;
                            gaps.push(gap);
                        }
                    });
                    const maxGap = gaps.length > 0 ? Math.max(...gaps) : 0;
                    return { maxGap, totalWithNominees: gaps.length };
                }""")
                
                results.append({
                    "theme": theme,
                    "viewport": vp["name"],
                    "width": vp["width"],
                    "scroll_check": scroll_check,
                    "card_overflow_check": card_overflow_check,
                    "gap_check": gap_check,
                })
                
                context.close()
                
        browser.close()
        
    server.should_exit = True
    
    print("\n=== PLAYWRIGHT AWARDS LAYOUT PROOF ===")
    all_passed = True
    for r in results:
        sc = r["scroll_check"]
        cc = r["card_overflow_check"]
        gc = r["gap_check"]
        passed = sc["noHorizontalScroll"] and cc["overflowCount"] == 0
        if not passed:
            all_passed = False
        print(f"[{r['theme'].upper()}] {r['viewport']} (w={r['width']}):")
        print(f"  - document.scrollWidth ({sc['scrollWidth']}) <= window.innerWidth ({sc['innerWidth']}): {sc['noHorizontalScroll']}")
        print(f"  - Cards checked: {cc['totalCards']}, Child element overflows: {cc['overflowCount']}")
        print(f"  - Max gap between winner & nominees: {gc['maxGap']:.1f}px (no margin-top: auto / justify-end)")
        if cc['overflows']:
            print(f"    Overflow details: {cc['overflows']}")
            
    print(f"\nFinal Result: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    return all_passed

if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
