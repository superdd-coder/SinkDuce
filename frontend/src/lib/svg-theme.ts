/** Display-only migration of the palette previously recommended to the model.
 * Only paint values change. Unknown/semantic colors, labels, geometry, references
 * and the saved/copied source retain their meaning. This is not a sanitizer;
 * the result must remain behind the passive <img> boundary in svg-preview.ts.
 */
const legacyPalette: Record<string, string> = {
  "#fdfbf7": "#fafafa",
  "#2a2e2a": "#202020",
  "#1a5e3d": "#174c39",
  "#0f4a2d": "#123e2e",
  "#e8f1eb": "#e9efec",
  "#e8f0eb": "#e9efec",
  "#dcebe2": "#dfe9e4",
  "#f6efe2": "#f1ece4",
  "#8a6d3b": "#806544",
}

const paintProperties = new Set(["fill", "stroke", "color", "stop-color", "flood-color", "lighting-color", "background", "background-color"])

function themePaint(value: string): string {
  const trimmed = value.trim()
  const hex = trimmed.match(/^#([\da-f]{6})([\da-f]{2})?$/i)
  if (hex) {
    const replacement = legacyPalette[`#${hex[1].toLowerCase()}`]
    return replacement ? value.replace(trimmed, replacement + (hex[2] ?? "")) : value
  }
  const rgb = trimmed.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(\s*,\s*(?:0?\.\d+|[01]))?\s*\)$/i)
  if (!rgb) return value
  const channels = rgb.slice(1, 4).map(Number)
  if (channels.some((channel) => channel > 255)) return value
  const replacement = legacyPalette["#" + channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")]
  if (!replacement) return value
  if (!rgb[4]) return value.replace(trimmed, replacement)
  const next = [1, 3, 5].map((offset) => parseInt(replacement.slice(offset, offset + 2), 16))
  return value.replace(trimmed, `rgba(${next.join(", ")}${rgb[4]})`)
}

function themeDeclarations(css: string): string {
  // Match whole declarations so selectors, quoted content and url(#paint) IDs
  // cannot be mistaken for colors. CSS comments are consumed unchanged.
  return css.replace(/\/\*[\s\S]*?\*\/|(^|[;{])(\s*[\w-]+\s*:\s*)([^;{}]*)(?=[;}\]]|$)/g,
    (declaration, separator: string | undefined, property: string, value: string) => {
      if (separator === undefined) return declaration
      const name = property.slice(0, property.indexOf(":")).trim().toLowerCase()
      if (!paintProperties.has(name)) return declaration
      const important = value.match(/\s*!important\s*$/i)?.[0] ?? ""
      const color = important ? value.slice(0, -important.length) : value
      return separator + property + themePaint(color) + important
    })
}

function themeTag(tag: string): string {
  const animatedPaint = /^<(?:animate|set)\b/i.test(tag)
    && /\battributeName\s*=\s*(["'])(?:fill|stroke|color|stop-color|flood-color|lighting-color)\1/i.test(tag)
  return tag.replace(/(\s+)([\w:.-]+)(\s*=\s*)(["'])([\s\S]*?)\4/g,
    (attribute, space: string, name: string, equals: string, quote: string, value: string) => {
      const key = name.toLowerCase()
      const next = key === "style" ? themeDeclarations(value)
        : paintProperties.has(key) ? themePaint(value)
          : animatedPaint && ["from", "to", "values"].includes(key)
            ? value.split(";").map(themePaint).join(";") : value
      return next === value ? attribute : space + name + equals + quote + next + quote
    })
}

export function themeSvgForPreview(source: string): string {
  // Lexical edits preserve original XML (including malformed candidates passed
  // to the image decoder). Never parse/insert untrusted SVG into the page DOM.
  return source.replace(/<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|<(?:script|foreignObject|text|tspan|title|desc)\b(?:[^"'<>]|"[^"]*"|'[^']*')*>[\s\S]*?<\/(?:script|foreignObject|text|tspan|title|desc)\s*>|<style\b(?:[^"'<>]|"[^"]*"|'[^']*')*>[\s\S]*?<\/style\s*>|<[A-Za-z_][\w:.-]*(?:[^"'<>]|"[^"]*"|'[^']*')*>/g,
    (token) => {
      if (/^<(?:!|\?|script\b|foreignObject\b)/i.test(token)) return token
      if (/^<style\b/i.test(token)) {
        return token.replace(/^(<style\b(?:[^"'<>]|"[^"]*"|'[^']*')*>)([\s\S]*)(<\/style\s*>)$/i,
          (_, open: string, css: string, close: string) => themeTag(open) + themeDeclarations(css) + close)
      }
      if (/^<(?:text|tspan|title|desc)\b/i.test(token)) {
        // Text labels are opaque, but text/tspan paint attributes still adapt.
        return token.replace(/<\/?(?:text|tspan|title|desc)\b(?:[^"'<>]|"[^"]*"|'[^']*')*>/gi, themeTag)
      }
      return themeTag(token)
    })
}
