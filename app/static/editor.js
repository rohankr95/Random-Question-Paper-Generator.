/* Turns every <textarea data-rich> into a formatting editor (bold, lists, tables, ...).
 * The server sanitises whatever is submitted, so this is purely a convenience. */
(function () {
  if (!window.Jodit) return; // editor failed to load: keep the plain textarea
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var buttons = [
    'bold', 'italic', 'underline', 'strikethrough', '|',
    'superscript', 'subscript', '|',
    'ul', 'ol', 'indent', 'outdent', '|',
    'table', '|',
    'align', '|',
    'symbols', 'hr', '|',
    'eraser', '|',
    'undo', 'redo', '|',
    'fullsize'
  ];
  document.querySelectorAll('textarea[data-rich]').forEach(function (ta) {
    // A hidden "required" textarea would block the form without telling the user;
    // the server checks for empty text instead.
    ta.removeAttribute('required');
    var editor = Jodit.make(ta, {
      theme: dark ? 'dark' : 'default',
      height: parseInt(ta.dataset.height || '260', 10),
      minHeight: 150,
      toolbarAdaptive: false,
      toolbarSticky: false,
      buttons: buttons,
      buttonsMD: buttons, buttonsSM: buttons, buttonsXS: buttons,
      // Paste from Excel / Word / Google Docs keeps tables and basic formatting,
      // without asking every time.
      askBeforePasteHTML: false,
      askBeforePasteFromWord: false,
      defaultActionOnPaste: 'insert_clear_html',
      defaultActionOnPasteFromWord: 'insert_clear_html',
      showCharsCounter: false,
      showWordsCounter: false,
      showXPathInStatusbar: false,
      showPoweredByJodit: false,
      spellcheck: true,
      disablePlugins: ['image', 'image-properties', 'image-processor', 'video', 'file', 'media',
        'iframe', 'drag-and-drop', 'drag-and-drop-element', 'ai-assistant', 'speech-recognize',
        'powered-by-jodit', 'about', 'print', 'preview'],
      table: { selectionCellStyle: 'border: 1px double #1e88e5 !important;' },
      placeholder: ''
    });
    // Always keep an empty line after a table at the end, so the setter can click
    // below a pasted table and carry on typing.
    function trailingParagraph() {
      var root = editor.editor, last = root.lastElementChild;
      if (last && last.nodeName === 'TABLE') {
        var p = editor.createInside.element('p');
        p.appendChild(editor.createInside.element('br'));
        root.appendChild(p);
      }
    }
    editor.events.on('afterPaste change afterInsertNode', function () { setTimeout(trailingParagraph, 0); });
  });
})();
