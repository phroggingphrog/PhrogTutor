import type { SuggestionKeyDownProps, SuggestionProps } from "@tiptap/suggestion";

export interface MenuItem {
  id: string;
  title: string;
  hint?: string;
}

interface MenuOptions {
  /** Accessible name of the list. */
  label: string;
  /** Shown when nothing matches; the menu closes instead when this is empty. */
  empty?: string;
}

let counter = 0;

/**
 * A small keyboard-driven popup for `@tiptap/suggestion`: arrows move, Enter/Tab pick, Escape
 * closes. The editor stays focused; the active option is announced through `aria-activedescendant`.
 */
export function menuRenderer<T extends MenuItem>({ label, empty }: MenuOptions) {
  return () => {
    const uid = `suggest-menu-${++counter}`;
    let box: HTMLDivElement | null = null;
    let items: T[] = [];
    let active = 0;
    let current: SuggestionProps<T, T> | null = null;

    const editorEl = () => current?.editor.view.dom as HTMLElement | undefined;

    const pick = (index: number) => {
      const item = items[index];
      if (item && current) current.command(item);
    };

    const paint = () => {
      if (!box) return;
      box.replaceChildren();
      if (!items.length) {
        const none = document.createElement("div");
        none.className = "suggest-menu-empty";
        none.textContent = empty ?? "";
        box.append(none);
      }
      items.forEach((item, index) => {
        const row = document.createElement("div");
        row.id = `${uid}-${index}`;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(index === active));
        row.className = "suggest-menu-item";
        const title = document.createElement("span");
        title.textContent = item.title;
        row.append(title);
        if (item.hint) {
          const hint = document.createElement("span");
          hint.className = "suggest-menu-hint";
          hint.textContent = item.hint;
          row.append(hint);
        }
        // mousedown, not click: the editor must not lose its selection before the command runs.
        row.addEventListener("mousedown", (event) => {
          event.preventDefault();
          pick(index);
        });
        box!.append(row);
      });
      const el = editorEl();
      if (el && items.length) {
        el.setAttribute("aria-controls", uid);
        el.setAttribute("aria-activedescendant", `${uid}-${active}`);
      }
      box.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    };

    const place = () => {
      const rect = current?.clientRect?.();
      if (!box || !rect) return;
      const height = box.offsetHeight;
      const below = rect.bottom + 6;
      const top = below + height > window.innerHeight && rect.top - height - 6 > 0 ? rect.top - height - 6 : below;
      box.style.top = `${top}px`;
      box.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - box.offsetWidth - 8))}px`;
    };

    const close = () => {
      const el = editorEl();
      el?.removeAttribute("aria-controls");
      el?.removeAttribute("aria-activedescendant");
      box?.remove();
      box = null;
    };

    const sync = (props: SuggestionProps<T, T>) => {
      current = props;
      items = props.items;
      if (!items.length && !empty) return close();
      if (!box) {
        box = document.createElement("div");
        box.id = uid;
        box.setAttribute("role", "listbox");
        box.setAttribute("aria-label", label);
        box.className = "suggest-menu";
        document.body.append(box);
      }
      active = Math.min(active, Math.max(items.length - 1, 0));
      paint();
      place();
    };

    return {
      onStart: (props: SuggestionProps<T, T>) => {
        active = 0;
        sync(props);
      },
      onUpdate: (props: SuggestionProps<T, T>) => {
        active = 0;
        sync(props);
      },
      onKeyDown: ({ event }: SuggestionKeyDownProps) => {
        if (!box) return false;
        if (event.key === "Escape") {
          close();
          return true;
        }
        if (!items.length) return false;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          active = (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
          paint();
          return true;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          pick(active);
          return true;
        }
        return false;
      },
      onExit: close,
    };
  };
}
