export type Page<T> = {
  rows: T[];
  page: number;
  pageCount: number;
  start: number;
  end: number;
};

export function paginate<T>(rows: T[], page: number, pageSize: number): Page<T> {
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(Math.max(1, page), pageCount);
  const offset = (currentPage - 1) * pageSize;
  const visibleRows = rows.slice(offset, offset + pageSize);
  return {
    rows: visibleRows,
    page: currentPage,
    pageCount,
    start: rows.length ? offset + 1 : 0,
    end: offset + visibleRows.length,
  };
}
