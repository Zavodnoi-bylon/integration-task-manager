import { escapeHtml } from "./utils.js";
import { normalizeLayout, packWidgets, resizedWidget, widgetDefinitions } from "./layout-model.js";

export function mountLayout(grid, toolbar, { layout, canEdit = false, onSave }) {
  let saved = normalizeLayout(layout);
  let draft = normalizeLayout(saved);
  let editing = false;
  let gesture = null;
  const nodes = new Map([...grid.querySelectorAll("[data-layout-id]")].map((node) => [node.dataset.layoutId, node]));
  const status = toolbar.querySelector("[data-layout-status]");
  toolbar.hidden = !canEdit;

  for (const definition of widgetDefinitions) {
    const widget = nodes.get(definition.id);
    const title = escapeHtml(definition.title);
    const tools = document.createElement("div");
    tools.className = "layout-widget-tools";
    tools.hidden = true;
    tools.innerHTML = `<button type="button" class="layout-drag" data-layout-drag aria-label="Переместить: ${title}. Стрелки перемещают блок по сетке."><span aria-hidden="true">⠿</span><span>${title}</span></button><label class="layout-width"><span class="sr-only">Ширина: ${title}</span><select data-layout-width aria-label="Ширина: ${title}">${Array.from({length: 13 - definition.minSpan}, (_, i) => i + definition.minSpan).map((span) => `<option value="${span}">${span === 12 ? "Вся ширина" : `${Math.round(span / 12 * 100)}%`}</option>`).join("")}</select></label><button type="button" class="layout-auto" data-layout-auto aria-label="Высота по содержимому: ${title}" title="Высота по содержимому">Авто</button>`;
    widget.prepend(tools);
    const resize = document.createElement("button");
    resize.type = "button";
    resize.className = "layout-resize";
    resize.dataset.layoutResize = "";
    resize.setAttribute("aria-label", `Изменить размер: ${definition.title}. Используйте мышь или стрелки.`);
    resize.innerHTML = '<span aria-hidden="true">◢</span>';
    resize.hidden = true;
    widget.append(resize);
  }

  let positions = [];
  let frame = null;
  function place(priority = null) {
    frame = null;
    const visible = draft.widgets.filter(item => !nodes.get(item.id).hidden);
    const heights = Object.fromEntries(visible.map(item => [item.id, nodes.get(item.id).getBoundingClientRect().height]));
    positions = packWidgets(visible, heights, priority || (gesture?.started && !gesture.resize ? gesture.id : null));
    for (const p of positions) {
      const node = nodes.get(p.id);
      const column = `${p.column} / span ${p.span}`;
      const row = `${p.row + 1} / span ${p.rows}`;
      if (node.style.gridColumn !== column) node.style.gridColumn = column;
      if (node.style.gridRow !== row) node.style.gridRow = row;
    }
  }
  function schedulePlace() { if (frame === null) frame = requestAnimationFrame(() => place()); }
  const observer = new ResizeObserver(schedulePlace);
  observer.observe(grid);
  for (const node of nodes.values()) observer.observe(node);
  function capturePositions() {
    for (const p of positions) {
      const item = draft.widgets.find(item => item.id === p.id);
      item.column = p.column; item.row = p.row;
    }
  }
  function apply() {
    for (const item of draft.widgets) {
      const node = nodes.get(item.id);
      // Move existing nodes: filters, calendar listeners and registry state survive.
      grid.append(node);
      node.style.setProperty("--widget-span", item.span);
      node.style.setProperty("--widget-height", item.height === null ? "0px" : `${item.height}px`);
      if (Number.isInteger(item.column)) item.column = Math.min(item.column, 13 - item.span);
      node.querySelector("[data-layout-width]").value = String(item.span);
    }
    place();
  }
  function setEditing(value) {
    editing = value;
    grid.classList.toggle("layout-editing", value);
    document.body.classList.toggle("layout-customizing", value);
    toolbar.querySelector("[data-layout-edit]").hidden = value;
    toolbar.querySelector("[data-layout-actions]").hidden = !value;
    toolbar.querySelector("[data-layout-help]").hidden = !value;
    for (const node of nodes.values()) {
      node.querySelector(".layout-widget-tools").hidden = !value;
      node.querySelector(".layout-resize").hidden = !value;
    }
    schedulePlace();
  }
  toolbar.addEventListener("click", async (event) => {
    if (!canEdit) return;
    if (event.target.closest("[data-layout-edit]")) {
      draft = normalizeLayout(saved); apply(); setEditing(true);
      status.textContent = "Перетащите блок в свободное место. Ширину и высоту меняйте за нижний угол.";
    } else if (event.target.closest("[data-layout-save]")) {
      const buttons = [...toolbar.querySelectorAll("button")];
      buttons.forEach(b => { b.disabled = true; });
      place(); capturePositions();
      const pending = normalizeLayout(draft);
      setEditing(false);
      status.textContent = "Сохраняем общую раскладку…";
      try {
        await onSave(pending);
        saved = pending;
        status.textContent = "Общая раскладка сохранена. Все увидят её при открытии или обновлении страницы.";
      } catch (error) {
        setEditing(true);
        status.textContent = error.message || "Не удалось сохранить раскладку на сервере.";
      } finally {
        buttons.forEach(b => { b.disabled = false; });
      }
    } else if (event.target.closest("[data-layout-cancel]")) {
      draft = normalizeLayout(saved); apply(); setEditing(false); status.textContent = "Изменения отменены.";
    } else if (event.target.closest("[data-layout-reset]")) {
      draft = normalizeLayout(null); apply();
      status.textContent = "Исходный вид восстановлен. Нажмите «Сохранить» или «Отмена».";
    }
  });
  grid.addEventListener("change", (event) => {
    if (!editing || !event.target.matches("[data-layout-width]")) return;
    const id = event.target.closest("[data-layout-id]").dataset.layoutId;
    draft.widgets.find((item) => item.id === id).span = Number(event.target.value);
    apply();
  });
  grid.addEventListener("click", (event) => {
    if (!editing || !event.target.closest("[data-layout-auto]")) return;
    const id = event.target.closest("[data-layout-id]").dataset.layoutId;
    draft.widgets.find((item) => item.id === id).height = null;
    apply();
  });

  function finishGesture(event) {
    if (!gesture || (event && event.pointerId !== gesture.pointerId)) return;
    const finished = gesture;
    gesture = null;
    finished.preview?.remove();
    if (finished.started) { place(finished.resize ? null : finished.id); capturePositions(); }
    finished.node.classList.remove("layout-moving", "layout-sizing");
    schedulePlace();
    if (grid.hasPointerCapture(finished.pointerId)) grid.releasePointerCapture(finished.pointerId);
    document.body.classList.remove("layout-gesture");
  }
  grid.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest("[data-layout-drag], [data-layout-resize]");
    if (!editing || !handle || event.button !== 0 || gesture) return;
    event.preventDefault();
    const node = handle.closest("[data-layout-id]");
    const item = draft.widgets.find((row) => row.id === node.dataset.layoutId);
    const bounds = node.getBoundingClientRect();
    gesture = {
      node, handle, pointerId: event.pointerId, id: item.id,
      x: event.clientX, y: event.clientY, resize: handle.hasAttribute("data-layout-resize"),
      initial: { span: item.span, height: bounds.height },
      offsetX: event.clientX - bounds.left, offsetY: event.clientY - bounds.top,
      columnWidth: (grid.getBoundingClientRect().width + 16) / 12,
    };
    grid.setPointerCapture(event.pointerId);
    document.body.classList.add("layout-gesture");
  });
  grid.addEventListener("pointermove", (event) => {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    if (Math.abs(dx) + Math.abs(dy) < 5 && !gesture.started) return;
    gesture.started = true;
    if (gesture.resize) {
      const item = draft.widgets.find((row) => row.id === gesture.id);
      const size = resizedWidget(item.id, gesture.initial, dx, dy, gesture.columnWidth);
      Object.assign(item, size);
      gesture.node.classList.add("layout-sizing");
      gesture.node.style.setProperty("--widget-span", size.span);
      gesture.node.style.setProperty("--widget-height", `${size.height}px`);
      const current = draft.widgets.find(row => row.id === gesture.id);
      if (Number.isInteger(current.column)) current.column = Math.min(current.column, 13 - current.span);
      gesture.node.querySelector("[data-layout-width]").value = String(size.span);
      place();
      status.textContent = `${widgetDefinitions.find((row) => row.id === item.id).title}: ${Math.round(size.span / 12 * 100)}% ширины, высота от ${size.height}px.`;
      return;
    }
    if (!gesture.preview) {
      gesture.preview = document.createElement("div");
      gesture.preview.className = "layout-drag-preview";
      gesture.preview.textContent = widgetDefinitions.find((row) => row.id === gesture.id).title;
      document.body.append(gesture.preview);
      gesture.node.classList.add("layout-moving");
    }
    gesture.preview.style.left = `${event.clientX + 14}px`;
    gesture.preview.style.top = `${event.clientY + 14}px`;
    if (matchMedia('(min-width: 901px)').matches) {
      const box = grid.getBoundingClientRect();
      const item = draft.widgets.find(item => item.id === gesture.id);
      item.column = Math.max(1, Math.min(13-item.span, Math.round((event.clientX-box.left-gesture.offsetX)/gesture.columnWidth)+1));
      item.row = Math.max(0, Math.min(10000, Math.round((event.clientY-box.top-gesture.offsetY)/12)));
      place();
      status.textContent = `Место: колонка ${item.column}. Соседние блоки сдвигаются без перекрытия.`;
    } else {
      // The mobile single-column reading order remains editable.
      const target = document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-layout-id]');
      if (target && target !== gesture.node && grid.contains(target)) {
        const item = draft.widgets.find(item => item.id === gesture.id);
        draft.widgets = draft.widgets.filter(row => row !== item);
        draft.widgets.splice(draft.widgets.findIndex(row => row.id === target.dataset.layoutId),0,item);
        apply();
      }
    }
    if (event.clientY < 90) window.scrollBy(0, -22);
    if (event.clientY > innerHeight - 80) window.scrollBy(0, 22);
  });
  grid.addEventListener("pointerup", finishGesture);
  grid.addEventListener("pointercancel", finishGesture);
  grid.addEventListener("lostpointercapture", finishGesture);
  grid.addEventListener("keydown", (event) => {
    if (!editing) return;
    const handle = event.target.closest("[data-layout-drag], [data-layout-resize]");
    if (!handle) return;
    const node = handle.closest("[data-layout-id]");
    const index = draft.widgets.findIndex((row) => row.id === node.dataset.layoutId);
    const item = draft.widgets[index];
    if (handle.hasAttribute("data-layout-drag") && event.key.startsWith('Arrow')) {
      event.preventDefault();
      const pos = positions.find(p => p.id === item.id);
      const steps = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-2],ArrowDown:[0,2]}[event.key];
      if (!pos || !steps) return;
      item.column = Math.max(1,Math.min(13-item.span,pos.column+steps[0]));
      item.row = Math.max(0,Math.min(10000,pos.row+steps[1]));
      positions = packWidgets(draft.widgets.filter(row=>!nodes.get(row.id).hidden),
        Object.fromEntries([...nodes].map(([id,node])=>[id,node.getBoundingClientRect().height])),item.id);
      capturePositions(); apply(); handle.focus({preventScroll:true});
    } else if (handle.hasAttribute("data-layout-resize") && event.key.startsWith("Arrow")) {
      event.preventDefault();
      const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -24], ArrowDown: [0, 24] }[event.key];
      if (!step) return;
      Object.assign(item, resizedWidget(item.id, {span:item.span, height:item.height ?? node.getBoundingClientRect().height}, step[0], step[1], 1));
      apply(); handle.focus({preventScroll:true});
    }
  });
  apply();
}
