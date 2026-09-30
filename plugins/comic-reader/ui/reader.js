// Comic Reader -- the reader.
//
// Two layouts:
//   pages  - side-by-side spreads: the cover alone, then pairs. A wide page (a
//            double-page spread saved as one image) always stands alone.
//            Narrow or portrait screens fall back to one page at a time.
//   scroll - webtoon: every page stacked in one continuous vertical strip.
//
// The layout comes from the gallery: the Webtoon tag means scroll; otherwise
// tall strip-shaped pages auto-detect as scroll, unless the user switched
// that comic to pages (remembered in its `comic_layout` custom field).
// Switching layout in the reader writes that choice back to the gallery, so
// it is visible and editable in Stash, not hidden in this browser.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;

  // Stash's .Lightbox supplies the backdrop, so a theme that restyles the
  // image viewer restyles the reader too.
  var ROOT = "Lightbox cr-reader";

  // The details panel docks beside the pages on screens wide enough to keep
  // a readable page next to it, and is a bottom sheet over the page below
  // that. Keep INFO_W in step with --cr-info-w in comics.css.
  var INFO_W = 340;
  var DOCK_MIN = 768;

  // The list's fields plus what the details panel shows.
  var READER_FIELDS = CR.GALLERY_FIELDS.replace("performers { id name }",
    "performers { id name disambiguation image_path }") + " details rating100 urls";

  function isWide(p) { return p.width > 0 && p.width > p.height * 1.1; }

  function buildSpreads(pages, double) {
    if (!double) return pages.map(function (_, i) { return [i]; });
    var spreads = [];
    var i = 0;
    if (pages.length) { spreads.push([0]); i = 1; } // the cover stands alone
    while (i < pages.length) {
      var next = pages[i + 1];
      if (!isWide(pages[i]) && next && !isWide(next)) {
        spreads.push([i, i + 1]);
        i += 2;
      } else {
        spreads.push([i]);
        i += 1;
      }
    }
    return spreads;
  }

  function spreadOf(spreads, page) {
    for (var s = 0; s < spreads.length; s++) {
      if (spreads[s].indexOf(page) >= 0) return s;
    }
    return 0;
  }

  function useViewport() {
    var s = React.useState({ w: window.innerWidth, h: window.innerHeight });
    React.useEffect(function () {
      function onResize() { s[1]({ w: window.innerWidth, h: window.innerHeight }); }
      window.addEventListener("resize", onResize);
      return function () { window.removeEventListener("resize", onResize); };
    }, []);
    return s[0];
  }

  // Toolbars show on mouse movement or a tap, and fade after a pause so the
  // page gets the whole screen.
  function useChrome() {
    var s = React.useState(true);
    var timer = React.useRef(null);
    var show = React.useCallback(function () {
      s[1](true);
      clearTimeout(timer.current);
      timer.current = setTimeout(function () { s[1](false); }, 2500);
    }, []);
    var toggle = React.useCallback(function () {
      clearTimeout(timer.current);
      s[1](function (v) { return !v; });
    }, []);
    React.useEffect(function () {
      show();
      return function () { clearTimeout(timer.current); };
    }, [show]);
    return { visible: s[0], show: show, toggle: toggle };
  }

  function Segmented(props) {
    return h("div", { className: "btn-group btn-group-sm cr-seg" },
      props.options.map(function (o) {
        return h("button", {
          key: o.value, type: "button", title: o.title || o.label,
          className: "btn " + (props.value === o.value ? "btn-primary" : "btn-secondary"),
          onClick: function (e) { e.stopPropagation(); props.onChange(o.value); },
        }, o.label);
      }));
  }

  // "Next in <series>" on the end card, when there is a next one.
  function NextUp(props) {
    var s = props.series;
    if (!s || !s.next) return null;
    return h("div", { className: "cr-next" },
      h("div", { className: "text-muted" }, "Next in " + s.series.name),
      h(CR.Button, { variant: "primary", onClick: function () { props.onOpen(s.next.id); } },
        CR.galleryTitle(s.next), " ", h(CR.Icon, { name: "faArrowRight" })));
  }

  // ------------------------------------------------------------ pages view

  function PagesView(props) {
    var pages = props.pages;
    var spreads = props.spreads;
    var si = props.spreadIndex;
    var atEnd = si >= spreads.length;
    var touch = React.useRef(null);

    function onClick(e) {
      // Relative to the stage, which the docked details panel narrows.
      var r = e.currentTarget.getBoundingClientRect();
      var x = (e.clientX - r.left) / Math.max(1, r.width);
      if (x < 0.3) props.go(-1);
      else if (x > 0.7) props.go(1);
      else props.chrome.toggle();
    }
    function onTouchStart(e) {
      var t = e.touches[0];
      touch.current = { x: t.clientX, y: t.clientY };
    }
    function onTouchEnd(e) {
      if (!touch.current) return;
      var t = e.changedTouches[0];
      var dx = t.clientX - touch.current.x;
      var dy = t.clientY - touch.current.y;
      touch.current = null;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
        e.preventDefault();
        props.go(dx < 0 ? 1 : -1);
      }
    }

    var body;
    if (atEnd) {
      body = h("div", { className: "cr-end-card", onClick: function (e) { e.stopPropagation(); } },
        h("h3", null, "The end"),
        h("p", { className: "text-muted" }, props.title),
        h(NextUp, { series: props.series, onOpen: props.onOpen }),
        h("div", { className: "cr-end-actions" },
          h(CR.Button, { variant: props.series && props.series.next ? "secondary" : "primary", onClick: props.onExit }, "Back to comics"),
          h(CR.Button, { variant: "secondary", onClick: function () { props.goTo(0); } }, "Read again")));
    } else {
      var spread = spreads[si];
      body = h("div", { className: "cr-spread" + (spread.length > 1 ? " cr-spread-double" : "") },
        spread.map(function (pi, n) {
          var p = pages[pi];
          return h("img", {
            key: p.id, src: p.src, alt: "Page " + (pi + 1), draggable: false,
            className: "cr-page" + (spread.length > 1 ? (n === 0 ? " cr-page-left" : " cr-page-right") : ""),
          });
        }));
    }
    return h("div", {
      className: "cr-stage cr-pages", onClick: onClick,
      onTouchStart: onTouchStart, onTouchEnd: onTouchEnd,
    }, body);
  }

  // ----------------------------------------------------------- scroll view

  function ScrollView(props) {
    var ref = React.useRef(null);
    var raf = React.useRef(0);
    var pages = props.pages;
    var moved = React.useRef(false);

    // Jump to the remembered page once, after the strip has laid out. Width
    // and height attributes give every <img> its box before it loads, so the
    // offset is right even though lazy images haven't arrived yet.
    React.useEffect(function () {
      var el = ref.current;
      if (!el || !props.startPage) return;
      var img = el.querySelector('[data-page="' + props.startPage + '"]');
      if (img) el.scrollTop = img.offsetTop;
    }, []);

    React.useEffect(function () {
      if (ref.current) ref.current.focus();
    }, []);

    function onScroll() {
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(function () {
        var el = ref.current;
        if (!el) return;
        var mid = el.scrollTop + el.clientHeight / 3;
        var imgs = el.querySelectorAll("img[data-page]");
        var current = 0;
        for (var i = 0; i < imgs.length; i++) {
          if (imgs[i].offsetTop <= mid) current = i; else break;
        }
        props.onPage(current, el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight));
      });
    }

    return h("div", {
      ref: ref, className: "cr-stage cr-scroll", tabIndex: 0, onScroll: onScroll,
      onMouseDown: function () { moved.current = false; },
      onMouseMove: function (e) { if (e.buttons) moved.current = true; },
      onClick: function () { if (!moved.current) props.chrome.toggle(); },
    },
      h("div", { className: "cr-strip", style: { maxWidth: props.width + "px" } },
        pages.map(function (p, i) {
          return h("img", {
            key: p.id, src: p.src, alt: "Page " + (i + 1), "data-page": i,
            width: p.width || undefined, height: p.height || undefined,
            loading: i < 3 ? "eager" : "lazy", draggable: false, className: "cr-strip-page",
          });
        }),
        h("div", { className: "cr-end-card cr-scroll-end", onClick: function (e) { e.stopPropagation(); } },
          h("h3", null, "The end"),
          h("p", { className: "text-muted" }, props.title),
          h(NextUp, { series: props.series, onOpen: props.onOpen }),
          h(CR.Button, { variant: props.series && props.series.next ? "secondary" : "primary", onClick: props.onExit }, "Back to comics"))));
  }

  // ---------------------------------------------------------------- reader

  // The comic's series and where it sits in it, or null.
  function loadSeries(gallery) {
    if (!gallery) return Promise.resolve(null);
    return CR.seriesList().then(function (list) {
      var s = CR.seriesOf(gallery.tags, list);
      if (!s) return null;
      return CR.seriesComics(s.id).then(function (comics) {
        var at = -1;
        comics.forEach(function (c, i) { if (String(c.id) === String(gallery.id)) at = i; });
        return { series: s, comics: comics, index: at,
                 prev: at > 0 ? comics[at - 1] : null,
                 next: at >= 0 && at + 1 < comics.length ? comics[at + 1] : null };
      });
    }).catch(function (e) { console.warn("[comic-reader] series lookup failed:", e); return null; });
  }

  function Reader(props) {
    var galleryId = String(props.galleryId);
    // Bumped when the comic's series changes from inside the reader.
    var reload = React.useState(0);
    var data = CR.useAsync(function () {
      return Promise.all([
        CR.tags(),
        CR.gql("query ($id: ID!) { findGallery(id: $id) { " + READER_FIELDS + " } }", { id: galleryId })
          .then(function (d) { return d.findGallery; }),
        CR.fetchPages(galleryId),
        CR.family(),
      ]).then(function (r) {
        return loadSeries(r[1]).then(function (series) {
          return { tags: r[0], gallery: r[1], pages: r[2], family: r[3], series: series };
        });
      });
    }, [galleryId, reload[0]]);

    var viewport = useViewport();
    var chrome = useChrome();
    var layoutOverride = React.useState(null);
    var spreadPref = React.useState(CR.store.get("spread", "auto"));
    var stripWidth = React.useState(CR.store.get("stripWidth", 800));
    var pos = React.useState(null); // current page index (first page of the spread)
    var scrollProgress = React.useState(0);
    var toast = CR.useToast();
    var rootRef = React.useRef(null);
    // Details: docked, it stays as the user last left it (open the first
    // time); as a sheet on a phone it would cover the page, so it starts shut.
    var docked = viewport.w >= DOCK_MIN;
    var infoPref = React.useState(CR.store.get("info", true));
    var sheetOpen = React.useState(false);
    var infoOpen = docked ? infoPref[0] : sheetOpen[0];
    var toggleInfo = React.useCallback(function () {
      if (docked) {
        infoPref[1](function (v) { CR.store.set("info", !v); return !v; });
      } else {
        sheetOpen[1](function (v) { return !v; });
      }
    }, [docked]);

    // The reader covers the whole window; stop the page behind it scrolling.
    React.useEffect(function () {
      var prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return function () { document.body.style.overflow = prev; };
    }, []);

    var d = data.data;
    var pages = d ? d.pages : [];
    var gallery = d ? d.gallery : null;
    var tags = d ? d.tags : null;

    // `gallery` is null for a deleted gallery or a stale bookmark; that must
    // reach the "not found" message below rather than throw here.
    var ok = !!(d && gallery);
    var detected = ok ? CR.looksLikeWebtoon(pages, tags.ratio) : false;
    var tagged = ok ? CR.hasTag(gallery.tags, tags.webtoon) : false;
    var pinnedPages = ok ? ((gallery.custom_fields || {}).comic_layout === "pages") : false;
    var layout = layoutOverride[0] || (tagged ? "scroll" : (detected && !pinnedPages ? "scroll" : "pages"));

    var stageW = viewport.w - (docked && infoOpen ? INFO_W : 0);
    var landscape = stageW >= viewport.h * 1.05 && stageW >= 900;
    var double = spreadPref[0] === "double" || (spreadPref[0] === "auto" && landscape);
    var spreads = React.useMemo(function () { return buildSpreads(pages, double); }, [pages, double]);

    // Resume from the page saved on the gallery (the same on every device).
    // A comic never read since progress started syncing falls back to the
    // page this browser remembered locally, the way 0.3 and earlier saved it.
    var opened = React.useRef(null);
    React.useEffect(function () {
      if (!d || !gallery || pos[0] !== null) return;
      var prog = CR.progressOf(gallery);
      var p = prog.page;
      if (p === null && !prog.readAt && !prog.finished) p = CR.store.get("page:" + galleryId, 0);
      if (!(p > 0 && p < pages.length)) p = 0;
      opened.current = p;
      pos[1](p);
    }, [d]);

    var page = typeof pos[0] === "number" ? pos[0] : (pos[0] === "end" ? Math.max(0, pages.length - 1) : 0);
    var spreadIndex = pos[0] === "end" ? spreads.length : spreadOf(spreads, page);

    // Stash's gallery "Add" tab and "Remove from gallery" fire no hooks, so
    // opening a comic re-checks its pages' Comic Page tags. Fire and forget:
    // it only affects the Images page, never what is being read.
    React.useEffect(function () {
      if (ok) CR.runOperation({ mode: "pages", galleryId: galleryId }).catch(function () {});
    }, [ok, galleryId]);

    // Save progress to the gallery: a moment after the page stops changing,
    // at once on reaching the end (finished, so the next read starts over),
    // and whatever is still pending when the reader closes or the tab hides.
    // Opening a comic and closing it again saves nothing.
    var pending = React.useRef(null);
    var saveTimer = React.useRef(null);
    var total = pages.length;
    var flush = React.useCallback(function () {
      clearTimeout(saveTimer.current);
      var p = pending.current;
      if (p === null) return;
      pending.current = null;
      CR.store.set("page:" + galleryId, 0); // the gallery holds it now
      CR.saveProgress(galleryId, p, total).catch(function (e) {
        console.warn("[comic-reader] couldn't save reading progress:", e);
      });
    }, [galleryId, total]);
    React.useEffect(function () {
      if (!d || pos[0] === null) return;
      if (opened.current !== undefined && pos[0] === opened.current) return;
      opened.current = undefined; // once moved, every page counts, even the first
      pending.current = pos[0];
      clearTimeout(saveTimer.current);
      if (pos[0] === "end") flush();
      else saveTimer.current = setTimeout(flush, 1500);
    }, [pos[0]]);
    React.useEffect(function () {
      function onHide() { if (document.visibilityState === "hidden") flush(); }
      document.addEventListener("visibilitychange", onHide);
      return function () { document.removeEventListener("visibilitychange", onHide); flush(); };
    }, [flush]);

    // Next / previous in the series replace this reader in the history, so
    // Back still returns to wherever the comic was first opened from.
    var openComic = React.useCallback(function (id) {
      flush();
      var to = CR.readerPath(id);
      var st = (props.history && props.history.location && props.history.location.state) || { cr: true };
      if (props.history) props.history.replace(to, st);
      else window.location.assign(to);
    }, [flush, props.history]);
    var series = d ? d.series : null;

    var exit = React.useCallback(function () {
      if (props.onExit) props.onExit();
    }, [props.onExit]);

    // Turn pages from the LATEST position, not the one this render saw: two
    // quick key presses can both land before React re-renders and re-binds
    // the key handler, and a closure over spreadIndex would count them once.
    var spreadsRef = React.useRef(spreads);
    spreadsRef.current = spreads;
    var go = React.useCallback(function (delta) {
      pos[1](function (cur) {
        var sp = spreadsRef.current;
        var si = cur === "end" ? sp.length : spreadOf(sp, cur || 0);
        var next = si + delta;
        if (next < 0 || !sp.length) return cur;
        if (next >= sp.length) return "end";
        return sp[next][0];
      });
    }, []);

    var goTo = function (p) { pos[1](Math.max(0, Math.min(pages.length - 1, p))); };

    // Preload the next two spreads so turning a page is instant.
    React.useEffect(function () {
      if (layout !== "pages") return;
      for (var s = spreadIndex + 1; s <= spreadIndex + 2 && s < spreads.length; s++) {
        spreads[s].forEach(function (pi) { var im = new Image(); im.src = pages[pi].src; });
      }
    }, [spreadIndex, spreads, layout]);

    React.useEffect(function () {
      function onKey(e) {
        if (e.target && /input|textarea|select/i.test(e.target.tagName)) return;
        // A dialog over the reader (Add to series) owns the keyboard: its Esc
        // closes it, and must not close the reader behind it too.
        if (document.body.classList.contains("modal-open") ||
            (e.target && e.target.closest && e.target.closest(".modal"))) return;
        // Alt+Left is the browser's Back, Ctrl+F is find: not page turns.
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (e.key === "Escape") {
          // An open sheet closes first; the docked panel is part of the page.
          if (!docked && infoOpen) toggleInfo(); else exit();
          return;
        }
        if (e.key === "f") { toggleFullscreen(); return; }
        if (e.key === "i") { toggleInfo(); return; }
        if (layout !== "pages") return; // the scroll strip scrolls natively
        // Space on a focused rating star rates; it doesn't turn the page.
        if (e.target && e.target.closest && e.target.closest(".cr-info")) return;
        var k = e.key;
        if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "d") { e.preventDefault(); go(1); }
        else if (k === "ArrowLeft" || k === "PageUp" || k === "a") { e.preventDefault(); go(-1); }
        else if (k === "Home") { e.preventDefault(); goTo(0); }
        else if (k === "End") {
          e.preventDefault();
          var sp = spreadsRef.current;
          if (sp.length) pos[1](sp[sp.length - 1][0]);
        }
      }
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, [go, layout, exit, docked, infoOpen, toggleInfo]);

    function toggleFullscreen() {
      var el = rootRef.current;
      if (!el) return;
      if (document.fullscreenElement) document.exitFullscreen();
      else if (el.requestFullscreen) el.requestFullscreen();
    }

    function changeLayout(next) {
      if (next === layout) return;
      layoutOverride[1](next);
      var work;
      if (next === "scroll") {
        work = CR.editGalleryTags(galleryId, [tags.webtoon], []).then(function () {
          if (pinnedPages) return CR.setCustomField(galleryId, "comic_layout", null);
        });
      } else {
        // Add Comic before removing Webtoon so it never stops being a comic.
        work = CR.editGalleryTags(galleryId, [tags.comic], [tags.webtoon]).then(function () {
          if (detected) return CR.setCustomField(galleryId, "comic_layout", "pages");
        });
      }
      work.then(function () {
        toast.success(next === "scroll" ? "Reads as a webtoon scroll (tagged Webtoon)" : "Reads as pages");
      }).catch(function (e) { toast.error(e); });
    }

    function changeSpread(v) { spreadPref[1](v); CR.store.set("spread", v); }
    function changeWidth(delta) {
      var w = Math.max(400, Math.min(2000, stripWidth[0] + delta));
      stripWidth[1](w);
      CR.store.set("stripWidth", w);
    }

    if (data.loading) return h("div", { className: ROOT }, h(CR.Loading, { text: "Opening comic…" }));
    if (data.error) return h("div", { className: ROOT }, h(CR.ErrorBox, { error: data.error }));
    if (!gallery) return h("div", { className: ROOT }, h("div", { className: "cr-loading" }, "Comic not found."));
    // Wait for the resume position so the scroll strip starts in the right place.
    if (pos[0] === null) return h("div", { className: ROOT }, h(CR.Loading, { text: "Opening comic…" }));
    if (!pages.length) {
      return h("div", { className: ROOT },
        h("div", { className: "cr-end-card" }, h("h3", null, "No pages"),
          h("p", { className: "text-muted" }, "This gallery has no images yet."),
          h(CR.Button, { variant: "primary", onClick: exit }, "Back")));
    }

    var title = CR.galleryTitle(gallery);
    var counter;
    if (layout === "pages") {
      if (spreadIndex >= spreads.length) counter = "End";
      else {
        var sp = spreads[spreadIndex];
        counter = (sp.length > 1 ? (sp[0] + 1) + "–" + (sp[1] + 1) : String(sp[0] + 1)) + " / " + pages.length;
      }
    } else {
      counter = (page + 1) + " / " + pages.length;
    }

    var topBar = h("div", { className: "cr-bar cr-bar-top" + (chrome.visible ? "" : " cr-hidden"),
                            onClick: function (e) { e.stopPropagation(); } },
      h("button", { type: "button", className: "btn btn-secondary btn-sm", onClick: exit, title: "Back (Esc)" },
        h(CR.Icon, { name: "faArrowLeft" }), " Back"),
      h("div", { className: "cr-title" },
        h("div", { className: "cr-title-main" }, title),
        gallery.studio ? h("div", { className: "cr-title-sub text-muted" }, gallery.studio.name) : null),
      h("div", { className: "cr-bar-group" },
        h("span", { className: "cr-counter" }, counter),
        h(Segmented, {
          value: layout, onChange: changeLayout,
          options: [
            { value: "pages", label: "Pages", title: "Side-by-side pages" },
            { value: "scroll", label: "Scroll", title: "Webtoon: one continuous scroll (tags the comic Webtoon)" },
          ],
        }),
        layout === "pages"
          ? h(Segmented, {
              value: spreadPref[0], onChange: changeSpread,
              options: [
                { value: "auto", label: "Auto", title: "Two pages on wide screens, one on narrow" },
                { value: "single", label: "1", title: "One page at a time" },
                { value: "double", label: "2", title: "Always two pages" },
              ],
            })
          : h("div", { className: "btn-group btn-group-sm" },
              h("button", { type: "button", className: "btn btn-secondary", title: "Narrower",
                            onClick: function () { changeWidth(-100); } }, "−"),
              h("button", { type: "button", className: "btn btn-secondary", title: "Wider",
                            onClick: function () { changeWidth(100); } }, "+")),
        h("button", { type: "button", className: "btn btn-secondary btn-sm" + (infoOpen ? " active" : ""),
                      title: infoOpen ? "Hide details (I)" : "Details (I)", "aria-pressed": infoOpen,
                      onClick: toggleInfo }, h(CR.Icon, { name: "faCircleInfo" })),
        h("button", { type: "button", className: "btn btn-secondary btn-sm", title: "Full screen (F)",
                      onClick: toggleFullscreen }, h(CR.Icon, { name: "faExpand" })),
        h("a", { className: "btn btn-secondary btn-sm", title: "Open the gallery in Stash",
                 href: "/galleries/" + galleryId,
                 onClick: function (e) {
                   if (props.history) { e.preventDefault(); props.history.push("/galleries/" + galleryId); }
                 } }, h(CR.Icon, { name: "faImages" }))));

    var bottom = layout === "pages"
      // (Tucked away under an open details sheet, which covers that edge.)
      ? h("div", { className: "cr-bar cr-bar-bottom" + (chrome.visible && (docked || !infoOpen) ? "" : " cr-hidden"),
                   onClick: function (e) { e.stopPropagation(); } },
          h("input", {
            type: "range", className: "cr-slider", min: 0, max: spreads.length - 1,
            value: Math.min(spreadIndex, spreads.length - 1),
            onChange: function (e) { pos[1](spreads[Number(e.target.value)][0]); },
          }))
      : h("div", { className: "cr-progress" }, h("div", { style: { width: (scrollProgress[0] * 100) + "%" } }));

    // Only a real mouse wakes the toolbars on movement: Android sends a
    // synthetic mousemove before every tap, which would show the bars just as
    // the tap's own toggle hides them again.
    var info = null;
    if (infoOpen && CR.ComicInfo) {
      info = h(CR.Boundary, { name: "comic details" },
        h(CR.ComicInfo, {
          gallery: gallery, family: d.family, pages: pages.length, layout: layout,
          history: props.history, docked: docked, onClose: toggleInfo,
          onRated: function (v) { gallery.rating100 = v; },
          series: series, onOpen: openComic,
          onSeriesChanged: function () { reload[1](function (n) { return n + 1; }); },
        }));
      if (!docked) info = h(React.Fragment, null, h("div", { className: "cr-info-backdrop", onClick: toggleInfo }), info);
    }

    return h("div", { className: ROOT + (docked && infoOpen ? " cr-with-info" : ""), ref: rootRef,
                      onPointerMove: function (e) { if (e.pointerType === "mouse") chrome.show(); } },
      layout === "pages"
        ? h(PagesView, { pages: pages, spreads: spreads, spreadIndex: spreadIndex, go: go, goTo: goTo,
                         chrome: chrome, title: title, onExit: exit, series: series, onOpen: openComic })
        : h(ScrollView, { key: "scroll-" + galleryId, pages: pages, width: stripWidth[0],
                          startPage: page, chrome: chrome, title: title, onExit: exit,
                          series: series, onOpen: openComic,
                          onPage: function (p, frac) {
                            scrollProgress[1](frac);
                            // At the very bottom it counts as finished, so the next read starts over.
                            var next = frac >= 0.995 ? "end" : p;
                            if (next !== pos[0]) pos[1](next);
                          } }),
      topBar,
      bottom,
      info);
  }

  CR.Reader = Reader;
  CR.buildSpreads = buildSpreads;
})();
