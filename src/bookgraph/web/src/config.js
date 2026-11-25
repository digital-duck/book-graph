// App-level settings for one book. `bookgraph build` rewrites this file from book.yaml (`site:`, `levels`, `models`);
// the values below are the defaults, which are concept-book-base's own behaviour.
export const appConfig = {
  // When set, Header renders this image instead of the i18n `app.title` text.
  logoImage: null,
  // Use UI strings of languages not yet reviewed — locales/ui.yaml status
  // `machine` (LLM-drafted by scripts/translate_locale.py) or `draft` (started
  // on the Manage → i18n tab). Off: those languages show the UI in English.
  showMachineLocales: false,
  // The level a chapter opens at when its catalog entry has no `default_level`.
  defaultLevel: 'intro',
  // The model the content panel starts on, and the models it offers ({value, label}; null = the built-in list).
  defaultModel: 'sonnet',
  models: null,
}
