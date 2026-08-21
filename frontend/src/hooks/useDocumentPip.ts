/**
 * The watchlist in a floating, always-on-top window.
 *
 * This is the one browser API that gives a web page what a desktop 看盤軟體 has:
 * Document Picture-in-Picture opens a real window the OS keeps above
 * everything else, so the board stays readable while the user works in another
 * application. A plain `window.open` popup cannot do that -- it is offered
 * alongside (see utils/view.ts) for browsers without the API.
 *
 * The window gets a *portal*, not a second React root: the board inside it has
 * to share the auth session, the query cache and the locale with the tab that
 * opened it, or it would poll on its own schedule and show a different number.
 * React attaches its event listeners to a portal's container, so clicks inside
 * the floating window work without any extra wiring.
 *
 * Chrome/Edge only as of writing; `supported` is false elsewhere and the
 * caller hides the button.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

interface DocumentPictureInPictureApi {
  requestWindow(options?: {
    width?: number
    height?: number
    disallowReturnToOpener?: boolean
  }): Promise<Window>
  readonly window: Window | null
}

declare global {
  interface Window {
    documentPictureInPicture?: DocumentPictureInPictureApi
  }
}

export const pipSupported =
  typeof window !== 'undefined' && 'documentPictureInPicture' in window

/**
 * Clone the opener's stylesheets into the new document.
 *
 * The window starts blank -- it inherits no CSS -- and there is no single place
 * to read "the app's stylesheet" from: Vite injects <style> elements in dev and
 * emits a <link> in a production build. Reading `cssRules` covers both, and the
 * `href` fallback covers a sheet the browser refuses to expose.
 */
function copyStyles(target: Document) {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const css = Array.from(sheet.cssRules)
        .map((rule) => rule.cssText)
        .join('\n')
      const style = target.createElement('style')
      style.textContent = css
      target.head.appendChild(style)
    } catch {
      // Cross-origin sheet: the rules are unreadable, but the URL is not.
      if (sheet.href) {
        const link = target.createElement('link')
        link.rel = 'stylesheet'
        link.href = sheet.href
        target.head.appendChild(link)
      }
    }
  }
}

export interface DocumentPip {
  supported: boolean
  /** The open window, or null. Render into `pipWindow.document.body`. */
  pipWindow: Window | null
  open: (size?: { width?: number; height?: number }) => Promise<void>
  close: () => void
}

export function useDocumentPip(): DocumentPip {
  const [pipWindow, setPipWindow] = useState<Window | null>(null)
  // The state is what re-renders the portal; the ref is what the unmount
  // cleanup can read without making the effect depend on the window.
  const current = useRef<Window | null>(null)

  const open = useCallback(async (size?: { width?: number; height?: number }) => {
    if (!window.documentPictureInPicture) return
    // Only one such window may exist per tab, so re-opening is a focus.
    if (current.current) {
      current.current.focus()
      return
    }

    const opened = await window.documentPictureInPicture.requestWindow({
      width: size?.width ?? 400,
      height: size?.height ?? 620,
    })
    copyStyles(opened.document)
    opened.document.documentElement.lang = document.documentElement.lang
    opened.document.title = document.title
    // styles.css hangs the theme off body; the floating window has no #root.
    opened.document.body.classList.add('pip-body')

    // Closed from the window's own chrome, not from our button.
    opened.addEventListener('pagehide', () => {
      current.current = null
      setPipWindow(null)
    })

    current.current = opened
    setPipWindow(opened)
  }, [])

  const close = useCallback(() => {
    current.current?.close()
    current.current = null
    setPipWindow(null)
  }, [])

  // A window outliving the page that portals into it would render a frozen
  // board with no way to refresh it.
  useEffect(() => () => current.current?.close(), [])

  return { supported: pipSupported, pipWindow, open, close }
}
