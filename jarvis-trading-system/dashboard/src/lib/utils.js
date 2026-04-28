/** Format a number as Indian rupees. If `signed`, prepend +/–. */
export function formatCurrency(value, signed = false) {
  if (value == null) return "—";
  const abs = Math.abs(value);
  const formatted = new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(abs);
  const sign = signed ? (value >= 0 ? "+" : "−") : value < 0 ? "−" : "";
  return `${sign}₹${formatted}`;
}

/** Tailwind class for coloring a P&L value. */
export function pnlClass(value) {
  if (value == null || value === 0) return "text-gray-400";
  return value > 0 ? "text-green-400" : "text-red-400";
}

/** Format a 0–1 fraction as a percentage string. */
export function formatPct(value) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/** Strip a "EnumName.VALUE" string down to just "VALUE". */
export function enumValue(str) {
  if (!str) return "";
  const dot = str.lastIndexOf(".");
  return dot >= 0 ? str.slice(dot + 1) : str;
}
