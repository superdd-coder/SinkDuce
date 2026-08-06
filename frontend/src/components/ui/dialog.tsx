"use client"

import * as React from "react"
import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { XIcon } from "lucide-react"

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
         * Dim via CSS class (pm-dialog-overlay--silk) or default bg.
         * Fade ONLY via opacity + Base UI data-open / data-starting-style /
         * data-ending-style — never animate-in/out keyframes (hard-cut).
         */
        "bg-black/15 supports-backdrop-filter:backdrop-blur-[2px]",
        "transition-opacity duration-[280ms] ease-[cubic-bezier(0.22,1,0.36,1)]",
        "data-starting-style:opacity-0 data-ending-style:opacity-0 data-closed:opacity-0",
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
  /** Optional backdrop classes (e.g. match silk dialog duration). */
  overlayClassName?: string
}) {
  return (
    <DialogPortal>
      <DialogOverlay className={overlayClassName} />
      <DialogPrimitive.Popup
        data-slot="dialog-content"
        className={cn(
          "pm-dialog fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)]",
          "-translate-x-1/2 -translate-y-1/2 gap-4 outline-none sm:max-w-sm",
          /* Shell = canvas (unified with workspace / All Files); nested cards stay white */
          "rounded-[var(--pm-r-lg)] bg-[var(--pm-canvas,#f6f5f1)] p-5",
          "text-[var(--pm-text)] font-[family-name:var(--pm-ff)] text-[13px] font-normal leading-normal",
          "border-0 shadow-[var(--pm-shadow)]",
          /*
           * Default compact dialogs: keyframe enter/exit.
           * Silk / workspace pass animate-none + CSS transitions in className
           * (must come after these so twMerge can drop animate-in/out).
           */
          "duration-300",
          "data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95",
          "data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
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
            <span className="sr-only">Close</span>
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
        /* No title band / hairline — shell canvas shows through */
        "flex flex-col gap-1.5 bg-transparent border-0 shadow-none",
        className
      )}
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
          Close
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
        /* Premium dialog chrome title — Geist 13 / ink / always uppercase */
        "font-[family-name:var(--pm-ff)] text-[13px] font-normal leading-none",
        "tracking-[0.04em] uppercase text-[var(--pm-ink)]",
        className
      )}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: DialogPrimitive.Description.Props) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "text-[13px] font-normal leading-normal text-[var(--pm-muted)]",
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
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
}
