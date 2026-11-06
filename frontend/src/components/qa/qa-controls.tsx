import { useId, useRef, type ReactNode } from "react"
import { Menu as MenuPrimitive } from "@base-ui/react/menu"
import { MoreHorizontal, Upload } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Menu, MenuItem } from "@/components/ui/menu"
import { SettingsSwitch } from "@/components/llm-provider/settings-switch"

export function QaField({ label, children }: { label: string; children: ReactNode }) {
  return <label className="qa-field"><span className="pm-field-label">{label}</span>{children}</label>
}

export function QaToggle({ label, checked, onChange, disabled }: {
  label: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean
}) {
  const id = useId()
  return <div className="qa-toggle">
    <SettingsSwitch id={id} label={label} checked={checked} onCheckedChange={onChange} disabled={disabled} />
    <label htmlFor={id}>{label}</label>
  </div>
}

export function QaFilePicker({ label, accept, disabled, onFile }: {
  label: string; accept: string; disabled?: boolean; onFile: (file: File) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  return <>
    <input ref={input} type="file" hidden accept={accept} disabled={disabled}
      onChange={event => {
        const file = event.target.files?.[0]
        event.target.value = ""
        if (file) onFile(file)
      }} />
    <Button type="button" variant="secondary" disabled={disabled} onClick={() => input.current?.click()}>
      <Upload />{label}
    </Button>
  </>
}

export function QaActionMenu({ label, children, icon = <MoreHorizontal />, disabled }: { label: string; children: ReactNode; icon?: ReactNode; disabled?: boolean }) {
  return <MenuPrimitive.Root>
    <MenuPrimitive.Trigger disabled={disabled} render={<Button variant="ghost" size="icon-sm" className="qa-menu-trigger" aria-label={label} title={label} />}>
      {icon}
    </MenuPrimitive.Trigger>
    <MenuPrimitive.Portal>
      <MenuPrimitive.Positioner sideOffset={6} align="end" className="z-[400]">
        <MenuPrimitive.Popup render={<Menu className="qa-action-menu" />}>
          {children}
        </MenuPrimitive.Popup>
      </MenuPrimitive.Positioner>
    </MenuPrimitive.Portal>
  </MenuPrimitive.Root>
}

export function QaMenuItem({ children, onClick, disabled, destructive }: {
  children: ReactNode; onClick: () => void; disabled?: boolean; destructive?: boolean
}) {
  return <MenuPrimitive.Item nativeButton render={<MenuItem destructive={destructive} />} disabled={disabled} onClick={onClick}>
    {children}
  </MenuPrimitive.Item>
}
