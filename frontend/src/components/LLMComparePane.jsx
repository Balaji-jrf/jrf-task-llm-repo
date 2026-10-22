import React, { useEffect, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import { fetchOcrDocument } from '../llmApi';

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

export default function LLMComparePane({ ocrApiUrl, citation, onClose }) {
  const [documentUrl, setDocumentUrl] = useState('');
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(Number(citation?.page) || 1);
  const [error, setError] = useState('');
  const isPdf = /\.pdf$/i.test(citation?.filename || '');

  useEffect(() => {
    let cancelled = false;
    setDocumentUrl('');
    setError('');
    fetchOcrDocument(ocrApiUrl, citation.document_id)
      .then((document) => {
        if (!document.s3_presigned_url) throw new Error('No document preview URL was returned.');
        if (!cancelled) setDocumentUrl(document.s3_presigned_url);
      })
      .catch((loadError) => {
        if (!cancelled) setError(loadError.message);
      });
    return () => { cancelled = true; };
  }, [citation.document_id, ocrApiUrl]);

  useEffect(() => {
    setPage(Number(citation.page) || 1);
  }, [citation]);

  if (!citation) return null;

  return (
    <section className="llm-compare" aria-label="Source document comparison">
      <header className="llm-compare-header">
        <div>
          <strong>{citation.filename}</strong>
          <span>Page {citation.page} · source {citation.source_id}</span>
        </div>
        <button type="button" onClick={onClose} aria-label="Close document comparison">×</button>
      </header>
      {error && <p className="llm-error" role="alert">{error}</p>}
      {documentUrl && isPdf && (
        <>
          <nav className="llm-page-nav" aria-label="Source document pages">
            <button type="button" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>
              Previous
            </button>
            <span>Page {page} of {pageCount || '…'}</span>
            <button
              type="button"
              disabled={!pageCount || page >= pageCount}
              onClick={() => setPage((current) => current + 1)}
            >
              Next
            </button>
          </nav>
          <div className="llm-pdf-page">
            <Document
              file={documentUrl}
              onLoadSuccess={({ numPages }) => {
                setPageCount(numPages);
                setPage(Math.min(Number(citation.page) || 1, numPages));
              }}
              onLoadError={(loadError) => setError(`Could not render the PDF: ${loadError.message}`)}
              loading={<p>Loading source document…</p>}
            >
              <div className="llm-pdf-page-wrap">
                <Page pageNumber={page} width={700} renderTextLayer={false} renderAnnotationLayer={false} />
                {page === Number(citation.page) && citation.bbox?.length === 4 && (
                  <div
                    className="llm-citation-highlight"
                    style={{
                      left: `${citation.bbox[0] * 100}%`,
                      top: `${citation.bbox[1] * 100}%`,
                      width: `${(citation.bbox[2] - citation.bbox[0]) * 100}%`,
                      height: `${(citation.bbox[3] - citation.bbox[1]) * 100}%`,
                    }}
                    title={`Highlighted source ${citation.source_id}`}
                  />
                )}
              </div>
            </Document>
          </div>
          <p className="llm-citation-excerpt">{citation.text}</p>
        </>
      )}
      {documentUrl && !isPdf && (
        <div className="llm-image-page">
          <img src={documentUrl} alt={citation.filename} />
          {citation.bbox?.length === 4 && (
            <div
              className="llm-citation-highlight"
              style={{
                left: `${citation.bbox[0] * 100}%`,
                top: `${citation.bbox[1] * 100}%`,
                width: `${(citation.bbox[2] - citation.bbox[0]) * 100}%`,
                height: `${(citation.bbox[3] - citation.bbox[1]) * 100}%`,
              }}
            />
          )}
        </div>
      )}
    </section>
  );
}
