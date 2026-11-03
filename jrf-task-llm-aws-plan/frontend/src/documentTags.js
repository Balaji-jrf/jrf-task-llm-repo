export const PRESET_DOCUMENT_TAGS = [
  'Historical Document',
  'Policy Document',
  'Grey Literature',
  'Speech / Transcript',
  'Archival Record',
];

export const UNTAGGED_FILTER = '__untagged__';

export function getDocumentTagOptions(documents) {
  const savedTags = new Set(documents.map((document) => document.doc_tag?.trim()).filter(Boolean));
  const customTags = [...savedTags]
    .filter((tag) => !PRESET_DOCUMENT_TAGS.includes(tag))
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

  return [
    { value: 'all', label: 'All tags' },
    ...PRESET_DOCUMENT_TAGS.map((tag) => ({ value: tag, label: tag })),
    ...customTags.map((tag) => ({ value: tag, label: tag })),
    { value: UNTAGGED_FILTER, label: 'Other (no tag)' },
  ];
}
