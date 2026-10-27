import React, { useEffect, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import { fetchOcrDocument } from '../llmApi';

// Use CDN-hosted worker — avoids .mjs MIME type issues on Amplify/CloudFront
pdfjs.GlobalWorkerOptions.workerSrc =
  `https://unpkg.com/pdfjs-dist@4.8.69/build/pdf.worker.min.mjs`;

export default function CitationPanel({ citation, onClose }) {
  const [docUrl, setDocUrl] = useState('');
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const isPdf = /\.pdf$/i.test(citation?.filename || '');

  useEffect(() => {
    if (!citation) return;
    let cancelled = false;
    setDocUrl('');
    setError('');
    setPage(Number(citation.page_start || citation.page) || 1);
    fetchOcrDocument(citation.document_id)
      .then((doc) => {
        if (!doc.s3_presigned_url) throw new Error('No preview URL returned.');
        if (!cancelled) setDocUrl(doc.s3_presigned_url);
      })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, [citation?.document_id]);

  useEffect(() => {
    if (citation) setPage(Number(citation.page_start || citation.page) || 1);
  }, [citation]);

  if (!citation) {
    return (
      <aside className="citation-panel">
        <div className="citation-panel-head">
          <h3>Source Viewer</h3>
        </div>
        <div className="citation-placeholder">
          <div className="citation-placeholder-icon">📄</div>
          <p>Click a citation to view source</p>
        </div>
      </aside>
    );
  }

  const citationPage = Number(citation.page_start || citation.page) || 1;
  const hasBbox = citation.bbox?.length === 4;

  return (
    <aside className="citation-panel">
      <div className="citation-panel-head">
        <h3>Source Viewer</h3>
        <button className="citation-panel-close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="citation-panel-meta">
        <strong>{citation.filename}</strong>
        <span>
          Pages {citation.page_start || citation.page}–{citation.page_end || citation.page}
          {' · '}{citation.source_id}
        </span>
      </div>

      {error && <p className="error-bar" style={{ margin: '10px 16px' }}>{error}</p>}

      {docUrl && (
        <>
          {isPdf && (
            <div className="citation-page-nav">
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Prev</button>
              <span>Page {page} {pageCount ? `of ${pageCount}` : ''}</span>
              <button disabled={!pageCount || page >= pageCount} onClick={() => setPage((p) => p + 1)}>Next →</button>
            </div>
          )}

          <div className="citation-panel-body">
            {isPdf ? (
              <div className="citation-doc-wrap">
                <Document
                  file={docUrl}
                  onLoadSuccess={({ numPages }) => {
                    setPageCount(numPages);
                    setPage(Math.min(citationPage, numPages));
                  }}
                  onLoadError={(e) => setError(`PDF error: ${e.message}`)}
                  loading={<p style={{ padding: 16, font: '11px DM Mono, monospace' }}>Loading document…</p>}
                >
                  <div className="citation-pdf-wrap">
                    <Page
                      pageNumber={page}
                      width={280}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                    />
                    {page === citationPage && hasBbox && (
                      <div
                        className="bbox-highlight"
                        style={{
                          left: `${citation.bbox[0] * 100}%`,
                          top: `${citation.bbox[1] * 100}%`,
                          width: `${(citation.bbox[2] - citation.bbox[0]) * 100}%`,
                          height: `${(citation.bbox[3] - citation.bbox[1]) * 100}%`,
                        }}
                        title={`Source: ${citation.source_id}`}
                      />
                    )}
                  </div>
                </Document>
              </div>
            ) : (
              <div className="citation-doc-wrap" style={{ position: 'relative' }}>
                <img src={docUrl} alt={citation.filename} style={{ display: 'block', maxWidth: '100%' }} />
                {hasBbox && (
                  <div
                    className="bbox-highlight"
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

            <div className="citation-excerpt">
              <p style={{ margin: 0, color: '#708078', font: '8px DM Mono, monospace', textTransform: 'uppercase', marginBottom: 6 }}>
                Extracted text
              </p>
              {citation.text}
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
