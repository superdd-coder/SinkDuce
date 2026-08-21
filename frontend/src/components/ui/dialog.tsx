"use client"

import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { XIcon } from "lucide-react"
import { useT } from "@/i18n/use-t"

function Dialog({ ...props }: DialogPrimitive.Root.Props) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger({ ...props }: DialogPrimitive.Trigger.Props) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogPortal({ ...props }: DialogPrimitive.Portal.Props) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" keepMounted {...props} />
}

function DialogClose({ ...props }: DialogPrimitive.Close.Props) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: DialogPrimitive.Backdrop.Props) {
  return (
    <DialogPrimitive.Backdrop
      data-slot="dialog-overlay"
      className={cn(
        "fixed inset-0 z-50",
        /*
         * System dialog mask — same clock as popup (280ms silk).
         * Opacity only; solid dim via .pm-dialog-overlay--silk CSS.
         * Never TW animate-in/out keyframes (hard cut / residual blur).
         */
        "pm-dialog-overlay--silk",
        className
      )}
      {...props}
    />
  )
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  overlayClassName,
  ...props
}: DialogPrimitive.Popup.Props & {
  showCloseButton?: boolean
  /** Extra backdrop classes (silk mask is always on by default). */
  overlayClassName?: string
}) {
  const t = useT()
  return (
    <DialogPortal>
      <DialogOverlay className={overlayClassName} />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        className={cn(
          "pm-dialog pm-dialog--silk fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)]",
          "-translate-x-1/2 -translate-y-1/2 gap-4 outline-none sm:max-w-sm",
          /* Shell = canvas (unified with workspace / All Files); nested cards stay white */
          "rounded-[var(--pm-r-lg)] bg-[var(--pm-canvas,#f6f5f1)] p-5",
          "text-[var(--pm-text)] font-[family-name:var(--pm-ff)] text-[13px] font-normal leading-normal",
          "border-0 shadow-[var(--pm-shadow)]",
          /*
           * System open/close for ALL dialogs (see index.css silk block):
           * opacity + tiny scale, 280ms, same ease both ways.
           * Kill TW keyframe enter/exit so nothing fights CSS transitions.
           * Do NOT set duration-0 — that zeros transition-duration and kills silk fade-out.
           */
          "animate-none data-open:animate-none data-closed:animate-none",
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close
            data-slot="dialog-close"
            render={
              <Button
                variant="ghost"
                className="absolute top-2.5 right-2.5"
                size="icon-sm"
              />
            }
          >
            <XIcon />
            <span className="sr-only">{t("common.close")}</span>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Popup>
    </DialogPortal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn(
        /* kicker (optional) → large serif title → description (optional) */
        "pm-dialog-header flex flex-col bg-transparent border-0 shadow-none",
        className
      )}
      {...props}
    />
  )
}

/**
 * Green uppercase domain label above DialogTitle (e.g. “Section”, “Meeting”).
 * Optional — title is large serif either way; kicker only adds the green line.
 */
function DialogKicker({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      data-slot="dialog-kicker"
      className={cn("pm-dialog-kicker", className)}
      {...props}
    />
  )
}

function DialogFooter({
  className,
  showCloseButton = false,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean
}) {
  const t = useT()
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
        "border-0 bg-transparent p-0 m-0 rounded-none",
        className
      )}
      {...props}
    >
      {children}
      {showCloseButton && (
        <DialogPrimitive.Close render={<Button variant="ghost" />}>
          {t("common.close")}
        </DialogPrimitive.Close>
      )}
    </div>
  )
}

function DialogTitle({ className, ...props }: DialogPrimitive.Title.Props) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(
        /* Large light serif display (system soft-dialog chrome). */
        "pm-dialog-title",
        className
      )}
      {...props}
    />
  )
}

/** Optional guidance under the title — omit when the dialog needs no copy. */
function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "pm-dialog-desc",
        "*:[a]:underline *:[a]:underline-offset-3 *:[a]:hover:text-[var(--pm-green)]",
        className
      )}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogKicker,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}
