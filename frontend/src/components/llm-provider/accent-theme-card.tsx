import { useId } from "react"
import { Check } from "lucide-react"
import { useT } from "@/i18n/use-t"
import { accentThemes } from "@/lib/accent-theme"
import { useAppStore } from "@/stores/app-store"
import "./accent-theme-card.css"

export function AccentThemeCard() {
  const t = useT()
  const id = useId()
  const { theme: selected, customHue } = useAppStore((state) => state.accentPreference)
  const setAccentTheme = useAppStore((state) => state.setAccentTheme)

  return (
    <section className="pm-settings-section">
      <div className="pm-settings-card">
        <div className="pm-settings-card-head-text">
          <h2 id={id} className="pm-settings-card-kicker">{t("settings.colorTheme")}</h2>
          <p id={`${id}-scope`} className="pm-settings-card-desc">{t("settings.colorThemeScope")}</p>
        </div>
        <div className="sd-theme-options" role="radiogroup" aria-labelledby={id} aria-describedby={`${id}-scope`}>
          {accentThemes.map((theme) => (
            <label key={theme} className="sd-theme-option" data-custom={theme === "custom" || undefined}>
              <input className="sr-only" type="radio" name={id} value={theme}
                checked={selected === theme} onChange={() => setAccentTheme(theme)} />
              <span className="sd-theme-sample" data-accent-theme={theme} aria-hidden="true" />
              <span>{t(`settings.colorThemes.${theme}`)}</span>
              {selected === theme && <Check size={14} strokeWidth={2} aria-hidden="true" />}
            </label>
          ))}
        </div>
        {selected === "custom" && (
          <div className="sd-theme-hue">
            <label htmlFor={`${id}-hue`}>{t("settings.themeHue")}</label>
            <input id={`${id}-hue`} type="range" min={0} max={359} value={customHue}
              aria-valuetext={`${customHue}°`}
              onChange={(event) => setAccentTheme("custom", event.currentTarget.valueAsNumber)} />
            <output htmlFor={`${id}-hue`}>{customHue}°</output>
          </div>
        )}
      </div>
    </section>
  )
}
