// Minimal, dependency-free SVG chart helpers. No CDN, no vendored library —
// the dashboard only needs a handful of chart shapes (line, stacked bar,
// gauge), so a small hand-rolled renderer avoids an external JS dependency
// entirely, which matters for a pod that may have restricted internet
// egress (the dashboard itself is served from inside the cluster; the only
// network dependency of the PAGE should be the API it's already talking to).

const SEVERITY_COLORS = { critical: "#dc2626", warning: "#d97706", info: "#2563eb" };

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

// series: { critical: [n,...], warning: [...], info: [...] }, xLabels: [str,...]
// seriesLabels (optional): { critical: "심각(critical)", ... } — when given,
// a legend row is rendered above the plot so each colored line is identifiable.
function lineChart(container, series, xLabels, seriesLabels) {
  container.innerHTML = "";

  // Legend: without it the three same-shape lines are indistinguishable —
  // this is the "which line is which value?" fix. Rendered as plain HTML
  // above the SVG (simpler and more accessible than SVG <text> swatches).
  if (seriesLabels) {
    const legend = document.createElement("div");
    legend.className = "chart-legend";
    for (const key of Object.keys(series)) {
      const item = document.createElement("span");
      item.className = "chart-legend-item";
      const swatch = document.createElement("span");
      swatch.className = "chart-legend-swatch";
      swatch.style.background = SEVERITY_COLORS[key] || "#888";
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(seriesLabels[key] || key));
      legend.appendChild(item);
    }
    container.appendChild(legend);
  }

  const width = container.clientWidth || 600;
  const height = 220;
  const padding = { top: 10, right: 10, bottom: 30, left: 30 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  const allValues = Object.values(series).flat();
  const maxY = Math.max(1, ...allValues);
  const n = xLabels.length;

  const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}` });

  // gridlines + y-axis labels
  const steps = 4;
  for (let i = 0; i <= steps; i++) {
    const y = padding.top + plotH - (plotH * i) / steps;
    svg.appendChild(svgEl("line", {
      x1: padding.left, x2: width - padding.right, y1: y, y2: y,
      stroke: "currentColor", opacity: 0.1,
    }));
    const label = svgEl("text", { x: 2, y: y + 4, "font-size": 10, opacity: 0.6 });
    label.textContent = Math.round((maxY * i) / steps);
    svg.appendChild(label);
  }

  const xOf = (i) => (n <= 1 ? padding.left : padding.left + (plotW * i) / (n - 1));
  const yOf = (v) => padding.top + plotH - (plotH * v) / maxY;

  for (const [severity, values] of Object.entries(series)) {
    if (!values.length) continue;
    const points = values.map((v, i) => `${xOf(i)},${yOf(v)}`).join(" ");
    svg.appendChild(svgEl("polyline", {
      points, fill: "none", stroke: SEVERITY_COLORS[severity] || "#888", "stroke-width": 2,
    }));
    values.forEach((v, i) => {
      svg.appendChild(svgEl("circle", { cx: xOf(i), cy: yOf(v), r: 2.5, fill: SEVERITY_COLORS[severity] || "#888" }));
    });
  }

  // x-axis labels (sparse if many points)
  const labelEvery = Math.max(1, Math.ceil(n / 8));
  xLabels.forEach((label, i) => {
    if (i % labelEvery !== 0) return;
    const t = svgEl("text", {
      x: xOf(i), y: height - 8, "font-size": 10, "text-anchor": "middle", opacity: 0.6,
    });
    t.textContent = label;
    svg.appendChild(t);
  });

  container.appendChild(svg);
}

// categories: [str,...], series: { up: [n,...], down: [n,...] }
function stackedBarChart(container, categories, series, colors) {
  container.innerHTML = "";
  const width = container.clientWidth || 600;
  const barHeight = 22;
  const gap = 8;
  const height = categories.length * (barHeight + gap) + 10;
  const maxTotal = Math.max(1, ...categories.map((_, i) =>
    Object.values(series).reduce((sum, arr) => sum + (arr[i] || 0), 0)));
  const labelW = 120;
  const plotW = width - labelW - 40;

  const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}` });

  categories.forEach((cat, i) => {
    const y = i * (barHeight + gap) + 5;
    const label = svgEl("text", { x: 0, y: y + barHeight / 2 + 4, "font-size": 11 });
    label.textContent = cat;
    svg.appendChild(label);

    let xOffset = labelW;
    for (const [key, values] of Object.entries(series)) {
      const v = values[i] || 0;
      const w = (v / maxTotal) * plotW;
      if (w > 0) {
        svg.appendChild(svgEl("rect", {
          x: xOffset, y, width: w, height: barHeight, fill: (colors && colors[key]) || "#888", rx: 3,
        }));
      }
      xOffset += w;
    }
  });

  container.appendChild(svg);
}

// value/max as a simple horizontal proximity-to-threshold gauge (e.g. RPO:
// time since last successful backup vs. the expected schedule interval).
function thresholdGauge(container, { value, max, label, unit }) {
  container.innerHTML = "";
  const width = container.clientWidth || 400;
  const height = 70;
  const trackY = 36;
  const trackH = 14;

  const ratio = Math.min(1, value / max);
  const color = ratio < 0.7 ? "#16a34a" : ratio < 1 ? "#d97706" : "#dc2626";

  const svg = svgEl("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  svg.appendChild(svgEl("rect", { x: 0, y: trackY, width, height: trackH, rx: 7, fill: "currentColor", opacity: 0.1 }));
  svg.appendChild(svgEl("rect", { x: 0, y: trackY, width: width * ratio, height: trackH, rx: 7, fill: color }));

  const labelText = svgEl("text", { x: 0, y: 16, "font-size": 12, opacity: 0.8 });
  labelText.textContent = `${label}: ${value.toFixed(1)}${unit} / ${max}${unit} 임계치`;
  svg.appendChild(labelText);

  container.appendChild(svg);
}
