import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from utils import (
    VR_LABELS,
    VR_OPTIONS,
    convert_value_for_vr,
    get_missing_required_paths,
    get_resource_path,
    is_output_overlapping,
    validate_hex_input,
)
from worker import process_dicom_worker


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tipwindow = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event: tk.Event | None = None) -> None:
        if self.tipwindow or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("tahoma", "9"),
        )
        label.pack(ipadx=5, ipady=2)

    def hide_tip(self, event: tk.Event | None = None) -> None:
        if self.tipwindow:
            self.tipwindow.destroy()
        self.tipwindow = None


class DicomEditorApp:
    FIXED_ROWS = 3

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DICOM Tag Editor")
        self.root.geometry("620x450")
        self.tag_entries: list[tuple] = []

        for col in range(5):
            self.root.grid_columnconfigure(col, weight=1 if col in (2, 3) else 0)
        self.root.grid_rowconfigure(3, weight=1)

        self.validate_hex = self.root.register(validate_hex_input)

        _temp_btn = tk.Button(self.root)
        self._default_btn_bg: str = _temp_btn.cget("bg")
        self._default_btn_active_bg: str = _temp_btn.cget("activebackground")
        _temp_btn.destroy()
        _temp_chk = tk.Checkbutton(self.root, indicatoron=False)
        self._default_select_color: str = _temp_chk.cget("selectcolor")
        _temp_chk.destroy()

        self._build_path_rows()
        self._build_scroll_area()
        self._build_tag_section_header()
        self._add_tag_row(deletable=False)
        self._build_action_row()

    def _build_path_rows(self) -> None:
        self.source_entry = tk.Entry(self.root, width=60)
        self.file_entry = tk.Entry(self.root, width=60)
        self.output_entry = tk.Entry(self.root, width=60)

        self._create_path_row("Source Folder:", 0, self.source_entry,
                              lambda: self.browse_folder(self.source_entry))
        self._create_path_row("Source File:", 1, self.file_entry,
                              lambda: self.browse_file(self.file_entry))
        self._create_path_row("Output Folder:", 2, self.output_entry,
                              lambda: self.browse_folder(self.output_entry))

    def _build_scroll_area(self) -> None:
        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.canvas.configure(yscrollcommand=self._on_scrollbar_set)
        self.canvas.grid(row=3, column=0, columnspan=5, sticky="nswe")

        self.scrollbar_container = tk.Frame(self.root, width=17)
        self.scrollbar_container.grid(row=3, column=5, sticky="ns")
        self.scrollbar_container.grid_propagate(False)

        self.scrollbar = ttk.Scrollbar(self.scrollbar_container, orient="vertical", command=self.canvas.yview)

        self.scrollable_frame = tk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")

        for col in range(5):
            self.scrollable_frame.grid_columnconfigure(col, weight=1 if col == 3 else 0)
        self.scrollable_frame.grid_columnconfigure(4, minsize=30)

        self.scrollable_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_scrollbar_set(self, lo: str, hi: str) -> None:
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.scrollbar.pack_forget()
        else:
            self.scrollbar.pack(fill="both", expand=True)
        self.scrollbar.set(lo, hi)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self.scrollbar.winfo_ismapped():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_tag_section_header(self) -> None:
        tk.Label(self.scrollable_frame, text="Tag Edit", font=("Helvetica", 16)).grid(
            row=0, column=0, columnspan=6, pady=(10, 0))
        tk.Frame(self.scrollable_frame, height=2, bd=1, relief=tk.SUNKEN).grid(
            row=1, column=0, columnspan=6, sticky="we", padx=10, pady=5)
        tk.Label(self.scrollable_frame, text="Group / Element").grid(row=2, column=1, padx=3, sticky="w")
        tk.Label(self.scrollable_frame, text="VR").grid(row=2, column=2, padx=3, sticky="w")
        tk.Label(self.scrollable_frame, text="New Value").grid(row=2, column=3, padx=3, sticky="w")

    def _build_action_row(self) -> None:
        self.add_edit_button = tk.Button(self.scrollable_frame, text="Add Edit", command=self.add_edit_row)
        self.run_button = tk.Button(self.scrollable_frame, text="Run", command=self.modify_dicom_tags)
        self.progress_label = tk.Label(self.scrollable_frame, text="")
        self._reposition_action_row()

    def _reposition_action_row(self) -> None:
        action_row = self.FIXED_ROWS + len(self.tag_entries)
        self.add_edit_button.grid(row=action_row, column=0, columnspan=2, pady=5, sticky="w", padx=(10, 0))
        self.run_button.grid(row=action_row, column=3, columnspan=2, pady=5, sticky="e", padx=(0, 10))
        self.progress_label.grid(row=action_row + 1, column=0, columnspan=5, pady=(2, 5), sticky="w", padx=(10, 0))

    def _create_path_row(self, label_text: str, row: int, entry_var: tk.Entry, browse_cmd: callable) -> None:
        tk.Label(self.root, text=label_text).grid(row=row, column=0, padx=(10, 2), pady=5, sticky="e")
        entry_var.grid(row=row, column=1, columnspan=3, padx=(0, 2), pady=5, sticky="we")
        tk.Button(self.root, text="Browse", command=browse_cmd).grid(
            row=row, column=4, padx=(2, 10), pady=5, sticky="w")

    def _add_tag_row(self, deletable: bool = True) -> None:
        row_index = self.FIXED_ROWS + len(self.tag_entries)

        mode_var = tk.BooleanVar(value=False)
        toggle_btn = tk.Checkbutton(
            self.scrollable_frame,
            text="Edit",
            variable=mode_var,
            indicatoron=False,
            width=4,
            bg=self._default_btn_bg,
            activebackground=self._default_btn_active_bg,
        )

        group_element_frame = tk.Frame(self.scrollable_frame)
        group_entry = tk.Entry(group_element_frame, width=6, validate="key",
                               validatecommand=(self.validate_hex, "%P"))
        element_entry = tk.Entry(group_element_frame, width=6, validate="key",
                                 validatecommand=(self.validate_hex, "%P"))
        group_entry.pack(side="left", padx=(0, 3))
        element_entry.pack(side="left")

        vr_combobox = ttk.Combobox(self.scrollable_frame, values=VR_OPTIONS, width=7)
        vr_combobox.set("Same")
        vr_tooltip = ToolTip(vr_combobox, VR_LABELS.get("Same", "Unknown VR"))

        def update_vr_tooltip(event: tk.Event, cb: ttk.Combobox = vr_combobox, tt: ToolTip = vr_tooltip) -> None:
            tt.text = VR_LABELS.get(cb.get(), "Unknown VR")

        vr_combobox.bind("<<ComboboxSelected>>", update_vr_tooltip)

        new_value_entry = tk.Entry(self.scrollable_frame, width=40)

        toggle_btn.config(command=lambda: self._on_toggle(
            mode_var, toggle_btn, vr_combobox, new_value_entry))

        if deletable:
            delete_button = tk.Button(
                self.scrollable_frame, text="-", width=2,
                command=lambda: self.delete_row(
                    toggle_btn, group_element_frame, vr_combobox, new_value_entry, delete_button),
            )
            delete_button.grid(row=row_index, column=4, padx=(2, 0), pady=2)
        else:
            delete_button = None

        toggle_btn.grid(row=row_index, column=0, padx=(10, 2), pady=2, sticky="w")
        group_element_frame.grid(row=row_index, column=1, padx=(0, 2), pady=2, sticky="w")
        vr_combobox.grid(row=row_index, column=2, padx=3, pady=2, sticky="w")
        new_value_entry.grid(row=row_index, column=3, padx=(3, 3), pady=2, sticky="we")

        self.tag_entries.append((mode_var, toggle_btn, group_entry, element_entry,
                                 vr_combobox, new_value_entry, delete_button))

    def _on_toggle(
        self,
        mode_var: tk.BooleanVar,
        toggle_btn: tk.Checkbutton,
        vr_combobox: ttk.Combobox,
        new_value_entry: tk.Entry,
    ) -> None:
        if mode_var.get():
            toggle_btn.config(text="Del", selectcolor="#ffcccc", activebackground="#ffaaaa")
            vr_combobox.config(state="disabled")
            new_value_entry.config(state="disabled")
        else:
            toggle_btn.config(text="Edit",
                              selectcolor=self._default_select_color,
                              activebackground=self._default_btn_active_bg)
            vr_combobox.config(state="normal")
            new_value_entry.config(state="normal")

    def _regrid_tag_rows(self) -> None:
        for idx, (_, toggle_btn, group_entry, _,
                  vr_combobox, new_value_entry, delete_button) in enumerate(self.tag_entries):
            row_index = self.FIXED_ROWS + idx
            toggle_btn.grid(row=row_index, column=0, padx=(10, 2), pady=2, sticky="w")
            group_entry.master.grid(row=row_index, column=1, padx=(0, 2), pady=2, sticky="w")
            vr_combobox.grid(row=row_index, column=2, padx=3, pady=2, sticky="w")
            new_value_entry.grid(row=row_index, column=3, padx=(3, 3), pady=2, sticky="we")
            if delete_button:
                delete_button.grid(row=row_index, column=4, padx=(2, 0), pady=2)
        self._reposition_action_row()

    def browse_folder(self, entry: tk.Entry) -> None:
        folder_path = filedialog.askdirectory()
        if folder_path:
            entry.delete(0, tk.END)
            entry.insert(0, folder_path)

    def browse_file(self, entry: tk.Entry) -> None:
        file_path = filedialog.askopenfilename(
            filetypes=[("DICOM Files", "*.dcm"), ("All Files", "*.*")]
        )
        if file_path:
            entry.delete(0, tk.END)
            entry.insert(0, file_path)

    def add_edit_row(self) -> None:
        self._add_tag_row(deletable=True)
        self._reposition_action_row()

    def delete_row(
        self,
        toggle_btn: tk.Checkbutton,
        group_element_frame: tk.Frame,
        vr_combobox: ttk.Combobox,
        new_value_entry: tk.Entry,
        delete_button: tk.Button,
    ) -> None:
        toggle_btn.grid_forget()
        group_element_frame.grid_forget()
        vr_combobox.grid_forget()
        new_value_entry.grid_forget()
        delete_button.grid_forget()
        self.tag_entries[:] = [t for t in self.tag_entries if t[2].master != group_element_frame]
        self._regrid_tag_rows()

    def modify_dicom_tags(self) -> None:
        source_folder = self.source_entry.get()
        source_file = self.file_entry.get()
        output_folder = self.output_entry.get()

        missing = get_missing_required_paths(source_folder, source_file, output_folder)
        if missing:
            messagebox.showerror("Error", f"Unfilled input box: {', '.join(missing)}")
            return
        if source_folder and source_file:
            messagebox.showerror("Error", "Please select either a folder or a file, not both.")
            return
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        edit_tags: list[tuple[int, int, str, str]] = []
        del_tags: list[tuple[int, int]] = []

        for idx, (mode_var, _, group_entry, element_entry,
                  vr_combobox, new_value_entry, _) in enumerate(self.tag_entries, start=1):
            group_str = group_entry.get().strip()
            element_str = element_entry.get().strip()

            if not group_str and not element_str:
                continue

            if not (group_str and element_str):
                messagebox.showwarning("Missing Input",
                                       f"Row {idx}: Both Group and Element must be filled.")
                continue

            try:
                group = int(group_str, 16)
                element = int(element_str, 16)
            except ValueError:
                messagebox.showerror(
                    "Error",
                    f"Row {idx}: Group '{group_str}' or Element '{element_str}' is not a valid hex value.")
                continue

            if mode_var.get():
                del_tags.append((group, element))
            else:
                value = new_value_entry.get()
                vr = vr_combobox.get().strip()
                if not value.strip():
                    proceed = messagebox.askokcancel(
                        "Empty Value",
                        f"Row {idx}: New Value is empty for Edit operation.\n"
                        "Press Run to write an empty value, or Cancel to skip this row."
                    )
                    if not proceed:
                        continue
                    value = ""
                edit_tags.append((group, element, vr, value))

        if not edit_tags and not del_tags:
            messagebox.showerror("Error", "No valid tag entries found.")
            return

        edit_ids = [(g, e) for g, e, _, _ in edit_tags]
        del_ids = [(g, e) for g, e in del_tags]

        conflicts = sorted(set(edit_ids) & set(del_ids))
        if conflicts:
            tag_list = ", ".join(f"({g:04X},{e:04X})" for g, e in conflicts)
            messagebox.showerror(
                "Conflict Error",
                f"The following tag(s) appear in both Edit and Del rows:\n{tag_list}\n\n"
                "Please resolve the conflict before running."
            )
            return

        edit_dupes = sorted({t for t in edit_ids if edit_ids.count(t) > 1})
        if edit_dupes:
            tag_list = ", ".join(f"({g:04X},{e:04X})" for g, e in edit_dupes)
            messagebox.showerror(
                "Duplicate Error",
                f"The following tag(s) appear in multiple Edit rows:\n{tag_list}\n\n"
                "It is unclear which value should be applied."
            )
            return

        del_dupes = sorted({t for t in del_ids if del_ids.count(t) > 1})
        if del_dupes:
            tag_list = ", ".join(f"({g:04X},{e:04X})" for g, e in del_dupes)
            messagebox.showerror(
                "Duplicate Error",
                f"The following tag(s) appear in multiple Del rows:\n{tag_list}")
            return

        files_to_process: list[str] = []
        if source_folder:
            for dir_root, _, files in os.walk(source_folder):
                files_to_process.extend(
                    os.path.join(dir_root, file)
                    for file in files if file.lower().endswith(".dcm")
                )
        elif source_file:
            files_to_process.append(source_file)

        if not files_to_process:
            messagebox.showerror("Error", "There are no DICOM files.")
            return

        overlap = is_output_overlapping(source_folder, source_file, output_folder)
        if overlap == "same":
            proceed = messagebox.askyesno(
                "Confirm",
                "The output folder is the same as the source folder.\n"
                "Original files will be overwritten. Continue?"
            )
            if not proceed:
                return
        elif overlap == "nested":
            proceed = messagebox.askyesno(
                "Confirm",
                "The output folder is inside the source folder.\n"
                "Files already saved to the output folder may be re-processed. Continue?"
            )
            if not proceed:
                return

        self.run_button.config(state=tk.DISABLED)
        self.add_edit_button.config(state=tk.DISABLED)
        self.progress_label.config(text=f"0/{len(files_to_process)} processing...")

        progress_queue: queue.Queue = queue.Queue()
        worker_thread = threading.Thread(
            target=process_dicom_worker,
            args=(files_to_process, edit_tags, del_tags, output_folder, progress_queue),
            daemon=True,
        )
        worker_thread.start()

        error_log: list[str] = []

        def process_queue() -> None:
            try:
                while True:
                    message = progress_queue.get_nowait()
                    msg_type = message[0]

                    if msg_type == "progress":
                        current, total = message[1], message[2]
                        self.progress_label.config(text=f"{current}/{total} processing...")
                    elif msg_type == "error":
                        error_log.append(message[1])
                    elif msg_type == "done":
                        failed_files, total = message[1], message[2]
                        if not error_log:
                            messagebox.showinfo("Success", "Operation Complete!")
                        else:
                            summary = "\n".join(error_log[:20])
                            if len(error_log) > 20:
                                summary += f"\n... and {len(error_log) - 20} more error(s)."
                            messagebox.showerror(
                                f"Completed with errors ({failed_files}/{total} failed)",
                                summary
                            )
                        self.run_button.config(state=tk.NORMAL)
                        self.add_edit_button.config(state=tk.NORMAL)
                        self.progress_label.config(text="")
                        return
            except queue.Empty:
                self.root.after(100, process_queue)

        self.root.after(100, process_queue)


def main() -> None:
    root = tk.Tk()
    try:
        root.iconbitmap(get_resource_path("editor.ico"))
    except Exception:
        pass
    app = DicomEditorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
