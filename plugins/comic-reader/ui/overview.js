// Comic Reader -- the page overview, over the reader.
//
// Every page as a thumbnail, labelled with its page number, file name and
// date -- the two things the order comes from, so a page in the wrong place
// shows why. Tap one to go to it. It is also where a comic's order is fixed:
// Order by (file path, the default, or post date) and Reorder (drag, or the
// arrows on a touch screen), saved on the gallery so it holds everywhere.
// The sync gives every image of a post the same title, so file names, not
// titles, are what tell pages apart here.
(function () {
  "use strict";

  var CR = window.ComicReader;
  if (!CR || !CR.React) return;
  var React = CR.React;
  var h = CR.h;

  var MODES = [
    { value: "path", label: "File path", title: "By file name and folder (the default)" },
    { value: "date", label: "Date", title: "By each page's post date, then file path" },
    { value: "custom", label: "Custom", title: "Your own order, from Reorder" },
  ];

  function move(list, from, to) {
    var next = list.slice();
    var item = next.splice(from, 1)[0];
    next.splice(Math.max(0, Math.min(next.length, to)), 0, item);
    return next;
  }
  CR.movePage = move;

  // props: pages (in reading order), current (indices on screen), order
  // ({mode, ids}), onJump(index), onClose(), onOrder(mode, ids) -> Promise
  function Overview(props) {
    var draft = React.useState(null); // page list while reordering
    var busy = React.useState(false);
    var dragFrom = React.useRef(null);
    var toast = CR.useToast();
    var reordering = !!draft[0];
    var list = draft[0] || props.pages;
    // File names repeat across posts (every post has a 1.jpg), so a comic
    // gathered from several folders shows each page's folder too.
    var showFolder = React.useMemo(function () { return CR.manyFolders(props.pages); }, [props.pages]);
    var current = {};
    (props.current || []).forEach(function (i) { current[i] = true; });

    // Bring the page on screen into view when the overview opens.
    var gridRef = React.useRef(null);
    React.useEffect(function () {
      var el = gridRef.current && gridRef.current.querySelector(".cr-thumb.cr-current");
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "center" });
    }, []);

    function apply(mode, ids, msg) {
      busy[1](true);
      return props.onOrder(mode, ids)
        .then(function () { busy[1](false); draft[1](null); if (msg) toast.success(msg); })
        .catch(function (e) { busy[1](false); toast.error(e); });
    }
    function setMode(mode) {
      if (mode === props.order.mode || reordering) return;
      if (mode === "custom" && !props.order.ids.length) return;
      apply(mode, props.order.ids, null);
    }
    function nudge(i, by) { draft[1](move(list, i, i + by)); }

    var thumbs = list.map(function (p, i) {
      var cls = "cr-thumb" + (current[props.pages.indexOf(p)] && !reordering ? " cr-current" : "");
      var attrs = {
        key: p.id, className: cls, title: p.path,
        onClick: reordering ? undefined : function () { props.onJump(props.pages.indexOf(p)); },
      };
      if (reordering) {
        attrs.draggable = true;
        attrs.onDragStart = function (e) { dragFrom.current = i; e.dataTransfer.effectAllowed = "move"; };
        attrs.onDragOver = function (e) { e.preventDefault(); };
        attrs.onDrop = function (e) {
          e.preventDefault();
          if (dragFrom.current !== null && dragFrom.current !== i) draft[1](move(list, dragFrom.current, i));
          dragFrom.current = null;
        };
      }
      return h("div", attrs,
        h("img", { src: p.thumb, alt: "Page " + (i + 1), loading: "lazy", draggable: false }),
        h("div", { className: "cr-thumb-label" },
          h("strong", null, i + 1),
          h("span", { className: "cr-thumb-file" }, p.file || "(no file name)"),
          showFolder ? h("span", { className: "cr-thumb-folder text-muted" }, CR.pageFolder(p.path)) : null,
          p.date ? h("span", { className: "text-muted" }, p.date) : null),
        reordering
          ? h("div", { className: "cr-thumb-move btn-group btn-group-sm" },
              h("button", { type: "button", className: "btn btn-secondary", disabled: i === 0, title: "Earlier",
                            onClick: function () { nudge(i, -1); } }, h(CR.Icon, { name: "faArrowLeft" })),
              h("button", { type: "button", className: "btn btn-secondary", disabled: i === list.length - 1, title: "Later",
                            onClick: function () { nudge(i, 1); } }, h(CR.Icon, { name: "faArrowRight" })))
          : null);
    });

    var controls = reordering
      ? [
          h(CR.Button, { key: "save", variant: "primary", size: "sm", disabled: busy[0],
                         onClick: function () {
                           apply("custom", list.map(function (p) { return String(p.id); }), "Page order saved on this comic");
                         } }, h(CR.Icon, { name: "faCheck" }), " Save order"),
          h(CR.Button, { key: "cancel", variant: "secondary", size: "sm", disabled: busy[0],
                         onClick: function () { draft[1](null); } }, "Cancel"),
        ]
      : [
          h("span", { key: "l", className: "text-muted" }, "Order by"),
          h("div", { key: "m", className: "btn-group btn-group-sm" },
            MODES.map(function (m) {
              var off = m.value === "custom" && !props.order.ids.length;
              return h("button", {
                key: m.value, type: "button", title: off ? "Use Reorder to make one" : m.title, disabled: busy[0] || off,
                className: "btn " + (props.order.mode === m.value ? "btn-primary" : "btn-secondary"),
                onClick: function () { setMode(m.value); },
              }, m.label);
            })),
          h(CR.Button, { key: "r", variant: "secondary", size: "sm", disabled: busy[0],
                         onClick: function () { draft[1](props.pages.slice()); } },
            h(CR.Icon, { name: "faGripVertical" }), " Reorder"),
        ];

    return h("div", { className: "cr-overview", onClick: function (e) { e.stopPropagation(); } },
      h("div", { className: "cr-overview-head" },
        h("h4", null, "Pages ", h("span", { className: "text-muted" }, props.pages.length)),
        h("div", { className: "cr-overview-controls" }, controls),
        h(CR.Button, { variant: "secondary", size: "sm", className: "cr-overview-close", title: "Close (G or Esc)",
                       onClick: props.onClose }, h(CR.Icon, { name: "faXmark" }))),
      reordering
        ? h("p", { className: "cr-overview-hint text-muted" },
            "Drag pages into place, or use the arrows. Save keeps this order on the comic, on every device; " +
            "pages added later go after it.")
        : null,
      h("div", { className: "cr-overview-grid", ref: gridRef }, thumbs));
  }

  CR.Overview = Overview;
})();
