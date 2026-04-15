import React, { useState, useEffect, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import { DataGrid } from "@mui/x-data-grid";
import Box from "@mui/material/Box";
import TextField from "@mui/material/TextField";
import Paper from "@mui/material/Paper";
import RefreshIcon from "@mui/icons-material/Refresh";
import Typography from "@mui/material/Typography";
import Papa from "papaparse";
import Modal from "@mui/material/Modal";
import Fade from "@mui/material/Fade";
import IconButton from "@mui/material/IconButton";
import CloseIcon from "@mui/icons-material/Close";
import VisibilityIcon from "@mui/icons-material/Visibility";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import Tooltip from '@mui/material/Tooltip';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import DownloadIcon from '@mui/icons-material/Download';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import Button from '@mui/material/Button';
import Drawer from '@mui/material/Drawer';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import Collapse from '@mui/material/Collapse';
import MenuIcon from '@mui/icons-material/Menu';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import ArrowBackIosNewIcon from '@mui/icons-material/ArrowBackIosNew';
import MenuItem from '@mui/material/MenuItem';
import EditIcon from '@mui/icons-material/Edit';
import Add from '@mui/icons-material/Add';
import LinearProgress from '@mui/material/LinearProgress';

// API URL configuration - supports both localhost and production
const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:5003';

/** Trim CSV row keys/values; kept flat for shallow Papa.parse callbacks (Sonar nesting). */
function sanitizeCsvDataRow(row) {
  const cleanedRow = {};
  Object.keys(row).forEach((key) => {
    const cleanKey = key.trim();
    cleanedRow[cleanKey] = row[key] ? row[key].trim() : '';
  });
  return cleanedRow;
}

/** Parse retained-earnings flow CSV; extracted to limit callback nesting (Sonar). */
function parseQuarterlyFlowCsvText(csvText) {
  return new Promise((resolve) => {
    const onComplete = (result) => {
      console.log("CSV parsing result:", result);
      if (!result.data || result.data.length === 0) {
        console.log("No CSV data found");
        resolve([]);
        return;
      }
      const cleanedData = result.data
        .filter((row) => row.company_symbol && row.company_symbol.trim() !== '')
        .map(sanitizeCsvDataRow);
      console.log("Cleaned CSV data:", cleanedData);
      resolve(cleanedData);
    };
    Papa.parse(csvText, {
      header: true,
      complete: onComplete,
      error: (error) => {
        console.error("Error parsing CSV data:", error);
        resolve([]);
      },
    });
  });
}

/** Matches backend src.config.reporting_period (Jan–Apr → prior calendar year for Tadawul columns). */
function defaultDashboardFiscalYear() {
  const d = new Date();
  return d.getMonth() < 4 ? d.getFullYear() - 1 : d.getFullYear();
}

function isPresentMetricValue(v) {
  return v !== undefined && v !== null && !(typeof v === 'string' && v.trim() === '');
}

/** Newest calendar year for keys like "Q1 2026" under a "Q1 " prefix. */
function pickNewestQuarterlyValueByPrefix(quarterlyMap, quarterLabel) {
  const prefix = `${quarterLabel} `;
  let bestYear = -Infinity;
  let bestVal;
  for (const k of Object.keys(quarterlyMap)) {
    if (!k.startsWith(prefix)) continue;
    const yr = Number.parseInt(k.slice(prefix.length).trim(), 10);
    if (Number.isNaN(yr)) continue;
    if (yr >= bestYear) {
      bestYear = yr;
      const v = quarterlyMap[k];
      if (isPresentMetricValue(v)) bestVal = v;
    }
  }
  return bestVal;
}

/**
 * Resolve scraped net profit for the grid: keys follow statement dates ("Q1 2026") while
 * fiscal_year_focus may be 2025 — try adjacent years, then newest year for that quarter.
 */
function lookupQuarterlyNetProfitValue(quarterlyMap, quarterFilter, focusYear) {
  if (!quarterlyMap || typeof quarterlyMap !== 'object') return undefined;
  const q = quarterFilter;
  if (!q) return undefined;
  const candidates = [`${q} ${focusYear}`, `${q} ${focusYear + 1}`, `${q} ${focusYear - 1}`];
  for (const k of candidates) {
    if (!Object.hasOwn(quarterlyMap, k)) continue;
    const v = quarterlyMap[k];
    if (isPresentMetricValue(v)) return v;
  }
  return pickNewestQuarterlyValueByPrefix(quarterlyMap, q);
}

/** CSV/Papa: never use `x || ''` for amounts — numeric 0 is falsy and would become empty. */
function metricFromCsvField(v) {
  if (v === undefined || v === null) return '';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '';
  return String(v).trim();
}

function isMetricCellMissing(value) {
  if (value === undefined || value === null) return true;
  if (typeof value === 'number') return Number.isNaN(value);
  const s = String(value).trim();
  return s === '' || s === 'null' || s === 'undefined';
}

const QUARTERS_Q1_Q4 = ['Q1', 'Q2', 'Q3', 'Q4'];

function buildFlowMapFromQuarterlyRows(quarterlyFlowData) {
  const flowMap = {};
  quarterlyFlowData.forEach((row) => {
    const symbol = row.company_symbol ? row.company_symbol.toString().trim() : '';
    const quarter = row.quarter ? row.quarter.toString().trim() : '';
    if (!symbol || !quarter) {
      return;
    }
    if (!flowMap[symbol]) {
      flowMap[symbol] = {};
    }
    flowMap[symbol][quarter] = {
      previous_value: metricFromCsvField(row.previous_value),
      current_value: metricFromCsvField(row.current_value),
      flow: metricFromCsvField(row.flow),
      flow_formula: row.flow_formula ? String(row.flow_formula).trim() : '',
      year: row.year ? String(row.year).trim() : '',
      foreign_investor_flow: metricFromCsvField(row.reinvested_earnings_flow),
      net_profit_foreign_investor: metricFromCsvField(row.net_profit_foreign_investor),
      distributed_profits_foreign_investor: metricFromCsvField(row.distributed_profits_foreign_investor),
    };
    console.log(`Mapped flow data for ${symbol} ${quarter}:`, {
      ...flowMap[symbol][quarter],
      net_profit_foreign_investor: row.net_profit_foreign_investor,
      distributed_profits_foreign_investor: row.distributed_profit_foreign_investor,
    });
  });
  return flowMap;
}

function mergeOwnershipWithQuarterlyFlow(foreignOwnershipData, flowMap, onEvidenceClick) {
  const mergedData = [];
  foreignOwnershipData.forEach((row, idx) => {
    const symbol = row.symbol ? row.symbol.toString().trim() : '';
    const flowData = flowMap[symbol] || {};
    QUARTERS_Q1_Q4.forEach((quarter) => {
      const quarterData = flowData[quarter] || {};
      const mergedRow = {
        ...row,
        company_symbol: symbol,
        previous_quarter_value: metricFromCsvField(quarterData.previous_value),
        current_quarter_value: metricFromCsvField(quarterData.current_value),
        flow: metricFromCsvField(quarterData.flow),
        flow_formula: quarterData.flow_formula ? String(quarterData.flow_formula).trim() : '',
        year: quarterData.year ? String(quarterData.year).trim() : '',
        foreign_investor_flow: metricFromCsvField(quarterData.foreign_investor_flow),
        net_profit_foreign_investor: metricFromCsvField(quarterData.net_profit_foreign_investor),
        distributed_profits_foreign_investor: metricFromCsvField(quarterData.distributed_profits_foreign_investor),
        quarter,
        id: `${symbol}_${quarter}_${idx}`,
        onEvidenceClick,
      };
      if (mergedData.length < 5 || symbol === '2222') {
        console.log(`Row ${mergedData.length} (${symbol} ${quarter}):`, {
          symbol: mergedRow.symbol,
          company_name: mergedRow.company_name,
          quarter: mergedRow.quarter,
          flow: mergedRow.flow,
          flow_formula: mergedRow.flow_formula,
        });
      }
      mergedData.push(mergedRow);
    });
  });
  return mergedData;
}

/** Grid header suffixes for previous / current quarter columns (replaces deep ternaries). */
function quarterHeaderLabels(quarterFilter, y) {
  const prevAnnual = y - 1;
  if (quarterFilter === 'Q1') {
    return { prevQuarterHeader: `${prevAnnual}Q4`, currentQuarterHeader: `${y}Q1` };
  }
  if (quarterFilter === 'Q2') {
    return { prevQuarterHeader: `${y}Q1`, currentQuarterHeader: `${y}Q2` };
  }
  if (quarterFilter === 'Q3') {
    return { prevQuarterHeader: `${y}Q2`, currentQuarterHeader: `${y}Q3` };
  }
  if (quarterFilter === 'Q4') {
    return { prevQuarterHeader: `${y}Q3`, currentQuarterHeader: `${y}Q4` };
  }
  return { prevQuarterHeader: `${prevAnnual}Q4`, currentQuarterHeader: `${y}Q1` };
}

function evidenceQuarterForPreviousColumn(quarterFilter, y) {
  const prevAnnual = y - 1;
  if (quarterFilter === 'Q1') return `Annual_${prevAnnual}`;
  if (quarterFilter === 'Q2') return `Q1_${y}`;
  if (quarterFilter === 'Q3') return `Q2_${y}`;
  return `Q3_${y}`;
}

function evidenceQuarterForCurrentColumn(quarterFilter, y) {
  if (quarterFilter === 'Q1') return `Q1_${y}`;
  if (quarterFilter === 'Q2') return `Q2_${y}`;
  if (quarterFilter === 'Q3') return `Q3_${y}`;
  return `Q4_${y}`;
}

function SarSignedAmountTypography({ raw }) {
  if (isMetricCellMissing(raw)) {
    return 'لايوجد';
  }
  const numValue = Number.parseFloat(raw);
  if (Number.isNaN(numValue)) {
    return raw;
  }
  const isPositive = numValue >= 0;
  const color = isPositive ? '#2e7d32' : '#d32f2f';
  const sign = numValue === 0 ? '' : (isPositive ? '+' : '');
  return (
    <Typography sx={{ color, fontWeight: 'bold' }}>
      {sign}{numValue.toLocaleString('en-US')} SAR
    </Typography>
  );
}

function EvidenceVisibilityButton({ onOpenEvidence, sx }) {
  return (
    <Tooltip title="عرض دليل الاستخراج - انقر لرؤية المستند الأصلي" arrow placement="top">
      <IconButton
        size="small"
        onClick={onOpenEvidence}
        sx={[
          {
            color: '#1e6641',
            '&:hover': { bgcolor: '#e8f5ee' },
            padding: '8px',
            minWidth: '40px',
            width: '40px',
            height: '40px',
          },
          ...(sx ? [sx] : []),
        ]}
      >
        <VisibilityIcon sx={{ fontSize: '16px' }} />
      </IconButton>
    </Tooltip>
  );
}

SarSignedAmountTypography.propTypes = {
  raw: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

EvidenceVisibilityButton.propTypes = {
  onOpenEvidence: PropTypes.func.isRequired,
  sx: PropTypes.oneOfType([PropTypes.object, PropTypes.array]),
};

/**
 * DataGrid column definitions (module scope lowers App() cognitive complexity / Sonar).
 */
function buildDashboardColumns(quarterFilter, yearFocus, netProfitData, fetchEvidenceData) {
  const y = yearFocus;
  const { prevQuarterHeader, currentQuarterHeader } = quarterHeaderLabels(quarterFilter, y);

  const renderPreviousRetainedWithEvidence = (params) => {
    const value = params.value;
    if (isMetricCellMissing(value)) {
      return 'لايوجد';
    }
    const numValue = Number.parseFloat(value);
    if (Number.isNaN(numValue)) {
      return value;
    }
    const eq = evidenceQuarterForPreviousColumn(quarterFilter, y);
    const handleOpen = (e) => {
      e.stopPropagation();
      fetchEvidenceData(params.row.symbol, eq);
    };
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography>{numValue.toLocaleString('en-US')}</Typography>
        <EvidenceVisibilityButton onOpenEvidence={handleOpen} />
      </Box>
    );
  };

  const renderCurrentRetainedWithEvidence = (params) => {
    const value = params.value;
    if (isMetricCellMissing(value)) {
      return 'لايوجد';
    }
    const numValue = Number.parseFloat(value);
    if (Number.isNaN(numValue)) {
      return value;
    }
    const eq = evidenceQuarterForCurrentColumn(quarterFilter, y);
    const handleOpen = (e) => {
      e.stopPropagation();
      fetchEvidenceData(params.row.symbol, eq);
    };
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography>{numValue.toLocaleString('en-US')}</Typography>
        <EvidenceVisibilityButton onOpenEvidence={handleOpen} />
      </Box>
    );
  };

  const renderNetProfitColumn = (params) => {
    const companySymbol = params.row.company_symbol ?? params.row.symbol;
    const key = companySymbol == null ? '' : String(companySymbol).trim();
    const companyNetProfit = key ? netProfitData[key] : undefined;

    if (companyNetProfit && companyNetProfit.quarterly_net_profit) {
      const value = lookupQuarterlyNetProfitValue(
        companyNetProfit.quarterly_net_profit,
        quarterFilter,
        y,
      );
      if (value !== undefined && value !== null && !(typeof value === 'string' && value.trim() === '')) {
        return typeof value === 'number' ? value.toLocaleString('en-US') : String(value);
      }
    }
    return 'لايوجد';
  };

  return [
    { field: 'symbol', headerName: 'رمز الشركة', width: 120, align: 'right', headerAlign: 'right' },
    { field: 'company_name', headerName: 'الشركة', width: 200, align: 'right', headerAlign: 'right' },
    { field: 'foreign_ownership', headerName: 'ملكية جميع المستثمرين الأجانب', width: 220, align: 'right', headerAlign: 'right' },
    { field: 'max_allowed', headerName: 'الملكية الحالية', width: 150, align: 'right', headerAlign: 'right' },
    { field: 'investor_limit', headerName: 'ملكية المستثمر الاستراتيجي الأجنبي', width: 220, align: 'right', headerAlign: 'right' },
    {
      field: 'previous_quarter_value',
      headerName: `الأرباح المبقاة للربع السابق (${prevQuarterHeader})`,
      width: 250,
      align: 'right',
      headerAlign: 'right',
      renderCell: renderPreviousRetainedWithEvidence,
    },
    {
      field: 'current_quarter_value',
      headerName: `الأرباح المبقاة للربع الحالي (${currentQuarterHeader})`,
      width: 250,
      align: 'right',
      headerAlign: 'right',
      renderCell: renderCurrentRetainedWithEvidence,
    },
    {
      field: 'flow',
      headerName: 'حجم الزيادة أو النقص في الأرباح المبقاة (التدفق)',
      width: 280,
      align: 'right',
      headerAlign: 'right',
      renderCell: (params) => <SarSignedAmountTypography raw={params.value} />,
    },
    {
      field: 'foreign_investor_flow',
      headerName: 'تدفق الأرباح المبقاة للمستثمر الأجنبي',
      width: 250,
      align: 'right',
      headerAlign: 'right',
      renderCell: (params) => <SarSignedAmountTypography raw={params.value} />,
    },
    {
      field: 'net_profit',
      headerName: 'صافي الربح',
      width: 150,
      align: 'right',
      headerAlign: 'right',
      renderCell: renderNetProfitColumn,
    },
    {
      field: 'net_profit_foreign_investor',
      headerName: 'صافي الربح للمستثمر الأجنبي',
      width: 220,
      align: 'right',
      headerAlign: 'right',
      renderCell: (params) => <SarSignedAmountTypography raw={params.value} />,
    },
    {
      field: 'distributed_profits_foreign_investor',
      headerName: 'الأرباح الموزعة للمستثمر الأجنبي',
      width: 250,
      align: 'right',
      headerAlign: 'right',
      renderCell: (params) => <SarSignedAmountTypography raw={params.value} />,
    },
  ];
}

function mergeCorrectionIntoRows(prevRows, updated) {
  return prevRows.map((row) => {
    if (row.symbol && updated.company_symbol && row.symbol.toString() === updated.company_symbol.toString()) {
      return {
        ...row,
        retained_earnings: updated.retained_earnings || updated.value || '',
        reinvested_earnings: updated.reinvested_earnings || '',
        year: updated.year || '',
        error: updated.error || '',
      };
    }
    return row;
  });
}

// Evidence Modal Component
const EvidenceModal = ({ open, onClose, evidenceData, loading, error, onDataUpdate, reportingYear }) => {
  const evidenceYearFallback = reportingYear ?? new Date().getFullYear();
  const [verifyMode, setVerifyMode] = useState(null); // null | 'confirm' | 'incorrect'
  const [correctionValue, setCorrectionValue] = useState("");
  const [correctionFeedback, setCorrectionFeedback] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const defaultEvidenceQuarterKey = `Q1_${evidenceYearFallback}`;
  const evidenceQuarterParam =
    evidenceData?.evidence?.requested_quarter || defaultEvidenceQuarterKey;
  const evidenceSymbolForUrl = evidenceData?.company_symbol ?? "";
  const evidencePngUrl = evidenceSymbolForUrl
    ? `${API_URL}/api/evidence/${evidenceSymbolForUrl}.png?quarter=${evidenceQuarterParam}&t=${Date.now()}`
    : "";

  return (
    <Modal
      open={open}
      onClose={onClose}
      aria-labelledby="evidence-modal-title"
      aria-describedby="evidence-modal-description"
    >
      <Box sx={{
        position: 'absolute',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: { xs: '95%', md: '70%' },
        maxWidth: 600,
        maxHeight: '80vh',
        bgcolor: 'background.paper',
        borderRadius: 3,
        boxShadow: 24,
        p: 3,
        overflow: 'auto',
        direction: 'rtl'
      }}>
        {/* Header */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
          <Typography id="evidence-modal-title" variant="h5" component="h2" sx={{ fontWeight: 'bold', color: '#1e6641' }}>
            دليل الاستخراج - الأرباح المبقاة
          </Typography>
          <IconButton onClick={onClose} sx={{ color: '#666' }}>
            <CloseIcon />
          </IconButton>
        </Box>

        {/* Content */}
        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', py: 4 }}>
            <CircularProgress sx={{ color: '#1e6641' }} />
            <Typography sx={{ ml: 2, color: '#666' }}>جاري تحميل الدليل...</Typography>
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {evidenceData && !loading && (
          <>
            {/* Screenshot */}
            {evidenceData.evidence?.has_evidence && (
              <Box sx={{ mb: 4 }}>
                <Box sx={{ 
                  display: 'flex', 
                  justifyContent: 'center',
                  border: '2px solid #e0e0e0',
                  borderRadius: 2,
                  overflow: 'auto',
                  bgcolor: '#fafafa',
                  maxHeight: '50vh'
                }}>
                  <img 
                    src={evidencePngUrl}
                    alt="Evidence Screenshot"
                    style={{ 
                      maxWidth: '100%', 
                      maxHeight: 'none',
                      objectFit: 'contain'
                    }}
                    onLoad={() => {
                      console.log('Evidence image loaded with quarter:', evidenceData.evidence?.requested_quarter);
                      console.log('Full image URL:', evidencePngUrl);
                    }}
                  />
                </Box>
              </Box>
            )}

            {/* Extracted numeric value */}
            {evidenceData.numeric_value && (
              <Box sx={{ mt: 3 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 'bold', color: '#1e6641', display: 'flex', alignItems: 'center', gap: 1 }}>
                      القيمة المستخرجة:
                  {evidenceData.applied_multiplier && Number(evidenceData.applied_multiplier) > 1 && (
                    <span style={{ color: '#888', fontWeight: 400 }}>(تم تطبيق تحويل الوحدة)</span>
                  )}
                    </Typography>
                <Typography variant="h6" sx={{ mt: 0.5 }}>
                  {Number(evidenceData.numeric_value).toLocaleString('en-US')} SAR
                </Typography>

                {/* Scaling explanation */}
                <Box sx={{ mt: 1.5, p: 1.5, bgcolor: '#f7f9f8', border: '1px solid #e0e6e4', borderRadius: 1.5 }}>
                  {(() => {
                    const rawStr = String(evidenceData.value || '').replace(/[^0-9,.-]/g, '');
                    const raw = Number(rawStr.replace(/,/g, ''));
                    const mult = Number(evidenceData.applied_multiplier || 1);
                    const unit = String(evidenceData.unit_detected || 'SAR');
                    const unitLabel = unit === 'million_SAR' ? 'بالملايين' : unit === 'thousand_SAR' ? 'بالآلاف' : unit === 'SAR' ? 'بالريال السعودي' : 'غير محدد';
                    if (!raw || mult === 1) {
                      return (
                        <Typography variant="body2" sx={{ color: '#4d4d4d' }}>
                          {unit === 'SAR' || mult === 1 ? 'القيم بالريال السعودي مباشرة (بدون تحويل).' : `الوحدة: ${unitLabel}`}
                        </Typography>
                      );
                    }
                    const result = raw * mult;
                    return (
                      <>
                        <Typography variant="body2" sx={{ color: '#4d4d4d' }}>
                          تم اكتشاف أن القيم {unitLabel}. تم تحويل القيمة كما يلي:
                        </Typography>
                        <Typography variant="body2" sx={{ mt: 0.5, direction: 'ltr', fontFamily: 'monospace', color: '#1e6641' }}>
                          {raw.toLocaleString('en-US')} × {mult.toLocaleString('en-US')} = {result.toLocaleString('en-US')}
                        </Typography>
                      </>
                    );
                  })()}
                  </Box>
              </Box>
            )}

            {/* Extraction Details */}
                  {evidenceData.extraction_method && (
                    <Box sx={{ 
                      p: 3,
                      bgcolor: '#f8f9fa',
                      borderRadius: 2,
                      border: '1px solid #e0e0e0'
                    }}>
                      <Typography sx={{ 
                        fontSize: '1rem',
                        color: '#666',
                        fontWeight: '500'
                      }}>
                        <strong>طريقة الاستخراج:</strong> {evidenceData.extraction_method}
                      </Typography>
              </Box>
            )}

            {/* Raw Text Context */}
            {evidenceData.context && (
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 'bold', mb: 2, color: '#1e6641' }}>
                  النص المستخرج
                </Typography>
                <Box sx={{ 
                  p: 2, 
                  bgcolor: '#f8f9fa', 
                  borderRadius: 2,
                  border: '1px solid #e0e0e0',
                  fontFamily: 'monospace',
                  fontSize: '14px',
                  whiteSpace: 'pre-wrap',
                  maxHeight: '200px',
                  overflow: 'auto'
                }}>
                  {evidenceData.context}
                </Box>
              </Box>
            )}
            {evidenceData && evidenceData.context && !loading && (
              <Box sx={{ mt: 2, textAlign: 'left' }}>
                <Tooltip title="تعديل النتيجة" arrow>
                  <IconButton
                    size="small"
                    sx={{ color: '#1e6641', opacity: 0.7, ml: 1, '&:hover': { opacity: 1, bgcolor: '#e8f5ee' } }}
                    onClick={() => setVerifyMode(verifyMode ? null : 'form')}
                  >
                    <InfoOutlinedIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                {verifyMode === 'form' && (
                  <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
                    <TextField
                      size="small"
                      label="القيمة الصحيحة"
                      value={correctionValue}
                      onChange={e => setCorrectionValue(e.target.value)}
                      sx={{ maxWidth: 180 }}
                    />
                    <TextField
                      size="small"
                      label="ملاحظات (اختياري)"
                      value={correctionFeedback}
                      onChange={e => setCorrectionFeedback(e.target.value)}
                      sx={{ maxWidth: 250 }}
                    />
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      sx={{ fontSize: 13, px: 2, py: 0.5, mt: 1, alignSelf: 'flex-start' }}
                      onClick={async () => {
                        setSubmitted(true);
                        // Send correction to backend
                        try {
                          const res = await fetch(`${API_URL}/api/correct_retained_earnings`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                              company_symbol: evidenceData.company_symbol || evidenceData.symbol,
                              correct_value: correctionValue,
                              feedback: correctionFeedback,
                            })
                          });
                          const data = await res.json();
                          if (data.status === 'success') {
                            // Update local view of value
                            evidenceData.value = correctionValue;
                            // Trigger dashboard refresh
                            if (typeof onDataUpdate === 'function') {
                              onDataUpdate();
                            }
                          }
                        } catch (e) {}
                        setVerifyMode(null);
                        // Close the modal after save
                        if (typeof onClose === 'function') onClose();
                      }}
                    >
                      إرسال التصحيح
                    </Button>
                    {submitted && (
                      <Typography sx={{ color: '#1e6641', fontSize: 14, mt: 1 }}>شكرًا لملاحظتك! تم تسجيل التصحيح.</Typography>
                    )}
                  </Box>
                )}
                {submitted && !verifyMode && (
                  <Typography sx={{ color: '#1e6641', fontSize: 14, mt: 1 }}>شكرًا لملاحظتك! تم تسجيل التصحيح.</Typography>
                )}
              </Box>
            )}
          </>
        )}
      </Box>
    </Modal>
  );
};

EvidenceModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func,
  evidenceData: PropTypes.shape({
    company_symbol: PropTypes.string,
    symbol: PropTypes.string,
    evidence: PropTypes.shape({
      has_evidence: PropTypes.bool,
      requested_quarter: PropTypes.string,
    }),
    numeric_value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    applied_multiplier: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
    unit_detected: PropTypes.string,
    context: PropTypes.string,
    extraction_method: PropTypes.string,
  }),
  loading: PropTypes.bool,
  error: PropTypes.oneOfType([PropTypes.string, PropTypes.node]),
  onDataUpdate: PropTypes.func,
  reportingYear: PropTypes.number,
};

// Edit Value Modal Component
const EditValueModal = ({ open, onClose, editData, onSave, loading }) => {
  const [newValue, setNewValue] = useState("");
  const [feedback, setFeedback] = useState("");

  // Update local state when editData changes
  useEffect(() => {
    if (editData) {
      setNewValue(editData.currentValue.toString());
      setFeedback("");
    }
  }, [editData]);

  const handleSave = () => {
    if (newValue.trim() === "") {
      alert("يرجى إدخال قيمة صحيحة");
      return;
    }
    onSave(editData.companySymbol, editData.fieldType, Number.parseFloat(newValue), feedback);
  };

  const getFieldDisplayName = (fieldType) => {
    const fieldNames = {
      'previous_quarter': 'الأرباح المبقاة للربع السابق',
      'current_quarter': 'الأرباح المبقاة للربع الحالي',
      'flow': 'حجم الزيادة أو النقص في الأرباح المبقاة (التدفق)',
      'foreign_investor_flow': 'تدفق الأرباح المبقاة للمستثمر الأجنبي',
      'net_profit_foreign_investor': 'صافي الربح للمستثمر الأجنبي',
      'distributed_profits_foreign_investor': 'الأرباح الموزعة للمستثمر الأجنبي'
    };
    return fieldNames[fieldType] || fieldType;
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      aria-labelledby="edit-modal-title"
      aria-describedby="edit-modal-description"
    >
      <Box sx={{
        position: 'absolute',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: { xs: '95%', md: '500px' },
        maxHeight: '90vh',
        bgcolor: 'background.paper',
        borderRadius: 3,
        boxShadow: 24,
        p: 3,
        overflow: 'auto',
        direction: 'rtl'
      }}>
        {/* Header */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
          <Typography id="edit-modal-title" variant="h5" component="h2" sx={{ fontWeight: 'bold', color: '#ff9800' }}>
            تعديل القيمة المستخرجة
          </Typography>
          <IconButton onClick={onClose} sx={{ color: '#666' }}>
            <CloseIcon />
          </IconButton>
        </Box>

        {/* Content */}
        {editData && (
          <Box>
            {/* Company Info */}
            <Box sx={{ mb: 3, p: 2, bgcolor: '#f8f9fa', borderRadius: 2 }}>
              <Typography variant="h6" sx={{ fontWeight: 'bold', mb: 1, color: '#1e6641' }}>
                معلومات الشركة
              </Typography>
              <Typography><strong>الرمز:</strong> {editData.companySymbol}</Typography>
              <Typography><strong>اسم الشركة:</strong> {editData.companyName}</Typography>
              <Typography><strong>الحقل:</strong> {getFieldDisplayName(editData.fieldType)}</Typography>
            </Box>

            {/* Current Value */}
            <Box sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ fontWeight: 'bold', mb: 2, color: '#666' }}>
                القيمة الحالية
              </Typography>
              <Typography sx={{ fontSize: '1.2rem', color: '#1e6641', fontWeight: 'bold' }}>
                {editData.currentValue.toLocaleString('en-US')} SAR
              </Typography>
            </Box>

            {/* New Value Input */}
            <Box sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ fontWeight: 'bold', mb: 2, color: '#ff9800' }}>
                القيمة الجديدة
              </Typography>
              <TextField
                fullWidth
                type="number"
                value={newValue}
                onChange={(e) => setNewValue(e.target.value)}
                placeholder="أدخل القيمة الصحيحة"
                sx={{ direction: 'ltr' }}
                inputProps={{
                  style: { textAlign: 'right', fontSize: '1.1rem' }
                }}
              />
            </Box>

            {/* Feedback Input */}
            <Box sx={{ mb: 3 }}>
              <Typography variant="h6" sx={{ fontWeight: 'bold', mb: 2, color: '#666' }}>
                ملاحظات (اختياري)
              </Typography>
              <TextField
                fullWidth
                multiline
                rows={3}
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="أضف ملاحظات حول التصحيح..."
                sx={{ direction: 'rtl' }}
              />
            </Box>

            {/* Actions */}
            <Box sx={{ display: 'flex', gap: 2, justifyContent: 'flex-end' }}>
              <Button 
                variant="outlined" 
                onClick={onClose}
                sx={{ color: '#666', borderColor: '#666' }}
              >
                إلغاء
              </Button>
              <Button 
                variant="contained" 
                onClick={handleSave}
                disabled={loading || newValue.trim() === ""}
                sx={{ 
                  bgcolor: '#ff9800', 
                  '&:hover': { bgcolor: '#f57c00' },
                  '&:disabled': { bgcolor: '#ccc' }
                }}
              >
                {loading ? <CircularProgress size={20} sx={{ color: 'white' }} /> : 'حفظ التصحيح'}
              </Button>
            </Box>
          </Box>
        )}
      </Box>
    </Modal>
  );
};

// Inline Editable Cell Component
const InlineEditableCell = ({ value, onSave, fieldType, companySymbol, companyName, initialValue }) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(value?.toString() || "");
  const [saving, setSaving] = useState(false);

  const handleEdit = () => {
    setIsEditing(true);
    setEditValue(value?.toString() || "");
  };

  const handleSave = async () => {
    if (editValue.trim() === "") return;
    
    setSaving(true);
    try {
      const response = await fetch(`${API_URL}/api/correct_field_value`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_symbol: companySymbol,
          field_type: fieldType,
          new_value: Number.parseFloat(editValue),
          feedback: 'Inline edit'
        }),
      });
      
      const data = await response.json();
      if (data.status === 'success') {
        onSave(Number.parseFloat(editValue));
        setIsEditing(false);
      } else {
        alert('فشل في حفظ التصحيح: ' + (data.message || ''));
      }
    } catch (error) {
      alert('حدث خطأ أثناء حفظ التصحيح: ' + error.message);
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditValue(value?.toString() || "");
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      handleSave();
    } else if (e.key === 'Escape') {
      handleCancel();
    }
  };

  if (isEditing) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <TextField
          size="small"
          type="number"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onKeyDown={handleKeyPress}
          autoFocus
          sx={{ 
            width: '120px',
            direction: 'ltr',
            '& .MuiInputBase-input': { 
              textAlign: 'right',
              fontSize: '0.9rem',
              padding: '4px 8px'
            }
          }}
        />
        <Tooltip title="حفظ التغيير" arrow placement="top">
          <IconButton 
            size="small" 
            onClick={handleSave}
            disabled={saving}
            sx={{ color: '#4caf50', '&:hover': { bgcolor: '#e8f5e8' } }}
          >
            {saving ? <CircularProgress size={16} /> : '✓'}
          </IconButton>
        </Tooltip>
        <Tooltip title="إلغاء التغيير" arrow placement="top">
          <IconButton 
            size="small" 
            onClick={handleCancel}
            sx={{ color: '#f44336', '&:hover': { bgcolor: '#ffebee' } }}
          >
            ✕
          </IconButton>
        </Tooltip>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Typography>{value?.toLocaleString('en-US') || 'لايوجد'}</Typography>
      <Tooltip title="هل تحتاج لتغيير هذه القيمة؟ انقر هنا للتعديل" arrow placement="top">
        <IconButton 
          size="small" 
          onClick={handleEdit}
          sx={{
            color: '#ff9800',
            opacity: 0.7,
            '&:hover': { bgcolor: '#fff3e0', opacity: 1 },
          }}
        >
          <EditIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  );
};

function App() {
  const [rows, setRows] = useState([]);
  const [search, setSearch] = useState("");
  const [quarterFilter, setQuarterFilter] = useState("Q1"); // Default to Q1 instead of "all"
  const [loading, setLoading] = useState(false);
  const [evidenceModalOpen, setEvidenceModalOpen] = useState(false);
  const [evidenceData, setEvidenceData] = useState(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [snapshotsError, setSnapshotsError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [snapshotsExpanded, setSnapshotsExpanded] = useState(false);
  const [userExports, setUserExports] = useState([]);
  const [userExportsLoading, setUserExportsLoading] = useState(false);
  const [userExportsError, setUserExportsError] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [fileToDelete, setFileToDelete] = useState(null);
  const [netProfitData, setNetProfitData] = useState({});
  // Commented out since we're using inline editing
  // const [editModalOpen, setEditModalOpen] = useState(false);
  // const [editData, setEditData] = useState(null);
  // const [editLoading, setEditLoading] = useState(false);
  const [customExportDate, setCustomExportDate] = useState("");
  const [customFileName, setCustomFileName] = useState("");
  const [customExportExpanded, setCustomExportExpanded] = useState(false);
  // Background jobs state
  const [pdfJobStatus, setPdfJobStatus] = useState({ status: 'idle' });
  const [netJobStatus, setNetJobStatus] = useState({ status: 'idle' });
  const [isPdfRunning, setIsPdfRunning] = useState(false);
  const [isNetRunning, setIsNetRunning] = useState(false);
  const [pdfPollId, setPdfPollId] = useState(null);
  const [netPollId, setNetPollId] = useState(null);
  const [pdfProgressOpen, setPdfProgressOpen] = useState(false);
  const [netProgressOpen, setNetProgressOpen] = useState(false);
  const [pdfPhase, setPdfPhase] = useState('running'); // 'running' | 'finalizing'
  const [netPhase, setNetPhase] = useState('running'); // 'running' | 'finalizing'
  /** Fiscal year the dashboard + evidence keys use (from /api/reporting_config; default matches backend). */
  const [reportingYear, setReportingYear] = useState(() => defaultDashboardFiscalYear());
  // Unified update modal state
  const [updateModalOpen, setUpdateModalOpen] = useState(false);
  const [selectPdf, setSelectPdf] = useState(false);
  const [selectNet, setSelectNet] = useState(false);
  const [chainNetAfterPdf, setChainNetAfterPdf] = useState(false); // kept for compatibility but unused when running both in parallel
  // Combined progress modal when running both
  const [bothProgressOpen, setBothProgressOpen] = useState(false);
  const [bothPdfRunning, setBothPdfRunning] = useState(false);
  const [bothNetRunning, setBothNetRunning] = useState(false);
  const [bothIsStopping, setBothIsStopping] = useState(false);

  const startPollPdf = (onComplete) => {
    if (pdfPollId) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/pdfs/status`);
        const data = await res.json();
        setPdfJobStatus(data);
        if (data.status === 'completed' || data.status === 'idle') {
          clearInterval(id);
          setPdfPollId(null);
          setIsPdfRunning(false);
          setPdfProgressOpen(false);
          // Trigger dashboard data reload (ensure backend has flushed files)
          setTimeout(() => {
            fetchData();
            // also refresh net profit map in case both was selected
            fetchNetProfitData();
          }, 300);
          if (typeof onComplete === 'function') {
            onComplete();
          }
        }
      } catch (e) {}
    }, 1500);
    setPdfPollId(id);
  };

  /** Combined tadawul job (one visit per company): poll until API marks completed or error — not idle. */
  const startPollBothCombined = () => {
    if (pdfPollId) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/pdfs/status`);
        const data = await res.json();
        setPdfJobStatus(data);
        setNetJobStatus(data);
        if (data.status === 'completed' || data.status === 'error') {
          clearInterval(id);
          setPdfPollId(null);
          setBothPdfRunning(false);
          setBothNetRunning(false);
          setBothProgressOpen(false);
          setBothIsStopping(false);
          if (data.status === 'error') {
            alert('❌ فشل التحديث المدمج: ' + (data.message || 'خطأ غير معروف'));
          }
          setTimeout(() => {
            fetchData();
            fetchNetProfitData();
          }, 300);
        }
      } catch (e) {}
    }, 1500);
    setPdfPollId(id);
  };

  const startPollNet = (onComplete) => {
    if (netPollId) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/net_profit/status`);
        const data = await res.json();
        setNetJobStatus(data);
        if (data.status === 'completed' || data.status === 'idle') {
          clearInterval(id);
          setNetPollId(null);
          setIsNetRunning(false);
          setNetProgressOpen(false);
          // Trigger dashboard data reload (ensure backend has flushed files)
          setTimeout(() => {
            // refresh net profit and flows
            fetchNetProfitData();
            fetchData();
          }, 300);
          if (typeof onComplete === 'function') {
            onComplete();
          }
        }
      } catch (e) {}
    }, 1500);
    setNetPollId(id);
  };

  useEffect(() => {
    return () => {
      if (pdfPollId) clearInterval(pdfPollId);
      if (netPollId) clearInterval(netPollId);
    };
  }, [pdfPollId, netPollId]);

  const fetchEvidenceData = useCallback(async (companySymbol, quarter) => {
    console.log(`Fetching evidence for ${companySymbol} quarter ${quarter}`);
    try {
      const response = await fetch(`${API_URL}/api/extractions/${companySymbol}?quarter=${quarter}`);
      if (response.ok) {
        const data = await response.json();
        console.log('Evidence data received:', data);
        console.log('Evidence quarter requested:', quarter);
        console.log('Evidence quarter in response:', data.evidence?.requested_quarter);
        console.log('Screenshot path:', data.evidence?.screenshot_path);
        setEvidenceData(data);
        setEvidenceModalOpen(true);
      } else {
        console.error('Failed to fetch evidence data');
      }
    } catch (error) {
      console.error('Error fetching evidence data:', error);
    }
  }, []);

  // Function to handle edit value button click - Commented out since we're using inline editing
  /* const handleEditValue = (companySymbol, fieldType, currentValue, companyName) => {
    setEditData({
      companySymbol,
      fieldType,
      currentValue,
      companyName,
      newValue: currentValue.toString()
    });
    setEditModalOpen(true);
  }; */

  // Function to fetch net profit data
  const fetchNetProfitData = async () => {
    try {
      const response = await fetch(`${API_URL}/api/net-profit`);
      if (response.ok) {
        const data = await response.json();
        setNetProfitData(data);
      } else {
        console.error('Failed to fetch net profit data');
      }
    } catch (error) {
      console.error('Error fetching net profit data:', error);
    }
  };

  // Function to handle evidence button click
  const handleEvidenceClick = (row) => {
    if (row.current_quarter_value && row.current_quarter_value !== "لايوجد") {
      setEvidenceModalOpen(true);
      fetchEvidenceData(row.symbol, row.quarter);
    }
  };

  // Function to handle reset (إعادة تعيين)
  const handleReset = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/api/refresh`, {
        method: 'POST',
      });
      const data = await response.json();
      if (data.status === 'success') {
        // Optionally show a success message
        fetchData(); // Reload data after refresh
      } else {
        alert('حدث خطأ أثناء التحديث: ' + (data.message || ''));
        setLoading(false);
      }
    } catch (error) {
      alert('تعذر الاتصال بالخادم: ' + error.message);
      setLoading(false);
    }
  };


  // Function to handle Excel export
  const handleExcelExport = async () => {
    try {
      const response = await fetch(`${API_URL}/api/export_excel?quarter=${quarterFilter}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      // Get the filename from the response headers
      const contentDisposition = response.headers.get('content-disposition');
      let filename = 'dashboard_table.xlsx';
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="(.+)"/);
        if (filenameMatch) {
          filename = filenameMatch[1];
        }
      }
      
      // Create blob and download
      const blob = await response.blob();
      const url = globalThis.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      globalThis.URL.revokeObjectURL(url);
      a.remove();
      
      // Refetch user exports so the new file appears in the sidebar
      setUserExportsLoading(true);
      fetch(`${API_URL}/api/user_exports`)
        .then(res => res.json())
        .then(data => {
          setUserExports(data);
          setUserExportsLoading(false);
        })
        .catch(err => {
          setUserExportsError('فشل في تحميل ملفات قام المستخدم بحفظها');
          setUserExportsLoading(false);
        });
    } catch (error) {
      console.error('Error exporting to Excel:', error);
      alert('فشل في تصدير ملف Excel: ' + error.message);
    }
  };

  const fetchData = () => {
    setLoading(true);
    
    // Load foreign ownership data (JSON)
    const loadForeignOwnership = fetch("/foreign_ownership_data.json")
      .then((res) => res.json())
      .catch((error) => {
        console.error("Error loading foreign ownership data:", error);
        return [];
      });

    // Load quarterly flow data (CSV) from backend API
    const loadQuarterlyFlowData = fetch(`${API_URL}/api/retained_earnings_flow.csv?t=${Date.now()}`)
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.text();
      })
      .then((csvText) => parseQuarterlyFlowCsvText(csvText))
      .catch((error) => {
        console.error("Error loading quarterly flow data:", error);
        return [];
      });

    // Combine both datasets
    Promise.all([loadForeignOwnership, loadQuarterlyFlowData])
      .then(([foreignOwnershipData, quarterlyFlowData]) => {
        console.log("Foreign ownership data count:", foreignOwnershipData.length);
        console.log("Quarterly flow data count:", quarterlyFlowData.length);

        const flowMap = buildFlowMapFromQuarterlyRows(quarterlyFlowData);
        console.log("Flow map keys:", Object.keys(flowMap));
        console.log("Sample flow data for 2222:", flowMap["2222"]);

        const mergedData = mergeOwnershipWithQuarterlyFlow(
          foreignOwnershipData,
          flowMap,
          handleEvidenceClick,
        );
        console.log("Final merged data sample:", mergedData.slice(0, 3));

        setRows(mergedData);
        setLoading(false);
      })
      .catch((error) => {
        console.error("Error combining data:", error);
        setLoading(false);
      });
  };

  // Fetch archived snapshots
  useEffect(() => {
    console.log('🔄 Fetching archived snapshots...');
    setSnapshotsLoading(true);
    fetch(`${API_URL}/api/ownership_snapshots`)
      .then(res => {
        console.log('📡 Snapshots response status:', res.status);
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then(data => {
        console.log('✅ Snapshots data received:', data);
        setSnapshots(data);
        setSnapshotsLoading(false);
      })
      .catch(err => {
        console.error('❌ Error fetching snapshots:', err);
        setSnapshotsError('فشل في تحميل ملفات الفترات السابقة');
        setSnapshotsLoading(false);
      });
  }, []);

  // Fetch user exports
  useEffect(() => {
    console.log('🔄 Fetching user exports...');
    setUserExportsLoading(true);
    fetch(`${API_URL}/api/user_exports`)
      .then(res => {
        console.log('📡 User exports response status:', res.status);
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then(data => {
        console.log('✅ User exports data received:', data);
        setUserExports(data);
        setUserExportsLoading(false);
      })
      .catch(err => {
        console.error('❌ Error fetching user exports:', err);
        setUserExportsError('فشل في تحميل ملفات قام المستخدم بحفظها');
        setUserExportsLoading(false);
      });
  }, []);

  // Function to fetch snapshots (for refreshing after quarterly archive)
  const fetchSnapshots = () => {
    console.log('🔄 Manual fetchSnapshots called...');
    setSnapshotsLoading(true);
    fetch(`${API_URL}/api/ownership_snapshots`)
      .then(res => {
        console.log('📡 Manual snapshots response status:', res.status);
        if (!res.ok) {
          throw new Error(`HTTP error! status: ${res.status}`);
        }
        return res.json();
      })
      .then(data => {
        console.log('✅ Manual snapshots data received:', data);
        setSnapshots(data);
        setSnapshotsLoading(false);
      })
      .catch(err => {
        console.error('❌ Error in manual fetchSnapshots:', err);
        setSnapshotsError('فشل في تحميل ملفات الفترات السابقة');
        setSnapshotsLoading(false);
      });
  };

  const getQuarterFromDate = (dateString) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    if (Number.isNaN(date.getTime())) return '';
    const month = date.getMonth() + 1;
    const year = date.getFullYear();
    const q = Math.ceil(month / 3);
    return `Q${q} ${year}`;
  };

  const dashboardColumns = useMemo(
    () => buildDashboardColumns(quarterFilter, reportingYear, netProfitData, fetchEvidenceData),
    [quarterFilter, reportingYear, netProfitData, fetchEvidenceData],
  );

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    fetchNetProfitData();
  }, []);

  useEffect(() => {
    fetch(`${API_URL}/api/reporting_config`)
      .then((res) => (res.ok ? res.json() : null))
      .then((cfg) => {
        if (cfg && typeof cfg.fiscal_year_focus === 'number') {
          setReportingYear(cfg.fiscal_year_focus);
        }
      })
      .catch(() => {});
  }, []);

  // Expose row update on globalThis so nested modal handlers can invoke it without prop drilling
  useEffect(() => {
    globalThis.updateRowAfterCorrection = (updated) => {
      setRows((prevRows) => mergeCorrectionIntoRows(prevRows, updated));
    };
    return () => { globalThis.updateRowAfterCorrection = undefined; };
  }, []);

  // Filter rows based on search and quarter filter
  const filteredRows = useMemo(() => {
    let filtered = rows;
    
    // Filter by quarter - now all companies have rows for all quarters
    filtered = filtered.filter(row => row.quarter === quarterFilter);
    
    console.log(`Showing ${filtered.length} companies for selected quarter`);
    
    // Then apply search filter
    if (search) {
      const searchLower = search.toLowerCase();
      filtered = filtered.filter((row) =>
        (row.symbol && row.symbol.toString().toLowerCase().includes(searchLower)) ||
        (row.company_name && row.company_name.toLowerCase().includes(searchLower))
      );
    }
    
    return filtered;
  }, [rows, search, quarterFilter]);

  // Delete handler
  const handleDeleteExport = (file) => {
    setFileToDelete(file);
    setDeleteDialogOpen(true);
  };

  const confirmDeleteExport = async () => {
    if (!fileToDelete) return;
    try {
      await fetch(`${API_URL}/api/user_exports/${fileToDelete.filename}`, { method: 'DELETE' });
      setUserExports((prev) => prev.filter(f => f.filename !== fileToDelete.filename));
    } catch (e) {}
    setDeleteDialogOpen(false);
    setFileToDelete(null);
  };

  const cancelDeleteExport = () => {
    setDeleteDialogOpen(false);
    setFileToDelete(null);
  };

  // Function to handle custom date export
  const handleCustomDateExport = async () => {
    try {
      setLoading(true);
      
      // Build the API URL with custom date and filename
      let apiUrl = `${API_URL}/api/export_excel?quarter=${quarterFilter}&custom_date=${customExportDate}`;
      if (customFileName.trim()) {
        apiUrl += `&custom_filename=${encodeURIComponent(customFileName.trim())}`;
      }
      
      const response = await fetch(apiUrl);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      // Get the filename from the response headers
      const contentDisposition = response.headers.get('content-disposition');
      let filename = 'dashboard_table.xlsx';
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="(.+)"/);
        if (filenameMatch) {
          filename = filenameMatch[1];
        }
      }
      
      // Create blob and download
      const blob = await response.blob();
      const url = globalThis.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      globalThis.URL.revokeObjectURL(url);
      a.remove();
      
      // Clear the custom inputs after successful export
      setCustomExportDate("");
      setCustomFileName("");
      
      // Refetch user exports so the new file appears in the sidebar
      setUserExportsLoading(true);
      fetch(`${API_URL}/api/user_exports`)
        .then(res => res.json())
        .then(data => {
          setUserExports(data);
          setUserExportsLoading(false);
        })
        .catch(err => {
          setUserExportsError('فشل في تحميل ملفات قام المستخدم بحفظها');
          setUserExportsLoading(false);
        });
        
      // Show enhanced success message
      const successMessage = `✅ تم التصدير بنجاح!\n\n📁 اسم الملف: ${filename}\n📅 التاريخ: ${customExportDate}\n🎯 الربع: ${getQuarterFromDate(customExportDate)}\n\nتم حفظ الملف في مجلد التنزيلات`;
      alert(successMessage);
      
    } catch (error) {
      console.error('Error exporting to Excel:', error);
      
      // Show enhanced error message
      let errorMessage = '❌ فشل في تصدير ملف Excel\n\n';
      
      if (error.message.includes('404')) {
        errorMessage += '🔍 السبب: لم يتم العثور على البيانات المطلوبة\n💡 الحل: تأكد من وجود البيانات للربع المحدد';
      } else if (error.message.includes('500')) {
        errorMessage += '🔧 السبب: خطأ في الخادم\n💡 الحل: حاول مرة أخرى أو اتصل بالدعم الفني';
      } else if (error.message.includes('fetch')) {
        errorMessage += '🌐 السبب: مشكلة في الاتصال\n💡 الحل: تأكد من تشغيل الخادم';
      } else {
        errorMessage += `🔍 السبب: ${error.message}\n💡 الحل: حاول مرة أخرى`;
      }
      
      alert(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box dir="rtl" sx={{ minHeight: "100vh", bgcolor: "#f4f6fa", fontFamily: "'Tajawal', 'Cairo', 'Noto Sans Arabic', sans-serif", display: 'flex', flexDirection: 'column' }}>
      {/* Sidebar menu button at the top right, no background */}
      { !drawerOpen && (
        <Box sx={{ width: '100%', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', pt: 1, pr: 0.2 }}>
          <IconButton
            onClick={() => setDrawerOpen(true)}
            sx={{
              bgcolor: 'transparent',
              boxShadow: 'none',
              borderRadius: 2,
              p: 0,
              '&:hover': { bgcolor: '#e3ecfa' },
            }}
            size="large"
            aria-label="فتح القائمة الجانبية"
          >
            <MenuIcon sx={{ color: '#1e6641', fontSize: 32 }} />
          </IconButton>
        </Box>
      )}

      {/* Main app container */}
      {/* Header with gradient */}
      <Box sx={{
        width: '100%',
        py: { xs: 3, md: 4 },
        px: 0,
        mb: 0,
        background: 'linear-gradient(90deg, #0d3b23 0%, #1e6641 100%)',
        boxShadow: '0 6px 24px 0 rgba(20, 83, 45, 0.18)',
        borderBottom: '4px solid #14532d',
        color: 'white',
        display: 'flex',
        alignItems: 'center',
        flexDirection: 'row', // logo on the right for RTL
        justifyContent: 'flex-start',
      }}>
        <img
          src="/sama-header.png"
          alt="Saudi Central Bank Logo"
          style={{
            height: '96px',
            width: 'auto',
            marginLeft: 0,
            marginRight: 0,
            display: 'block',
            flexShrink: 0,
            filter: 'drop-shadow(0 2px 8px rgba(0,0,0,0.08))',
            objectFit: 'contain',
          }}
        />
      </Box>
      {/* Title and subtitle below header */}
      <Box sx={{ textAlign: 'right', mt: { xs: 3, md: 5 }, mb: { xs: 3, md: 5 }, pr: { xs: 2, md: 8 } }}>
        <Typography variant="h3" fontWeight="bold" sx={{ mb: 1, fontSize: { xs: 26, md: 36 }, color: '#1e6641', display: 'inline-block' }}>
          جدول ملكية الأجانب والأرباح المبقاة
        </Typography>
        <Box sx={{ height: 4, width: 120, bgcolor: '#1e6641', mr: 0, ml: 'unset', borderRadius: 2, mb: 2 }} />
        <Typography variant="subtitle1" sx={{ color: '#37474f', fontSize: { xs: 15, md: 20 } }}>
          بيانات محدثة من تداول السعودية - ملكية الأجانب والأرباح المبقاة في الشركات المدرجة
        </Typography>
      </Box>

              {/* Main card */}
        <Paper elevation={4} sx={{
          maxWidth: '85vw',
          mx: 'auto',
          p: { xs: 2, md: 3 },
          borderRadius: 4,
          boxShadow: '0 6px 32px 0 rgba(30,102,65,0.10)',
          mb: 4,
          width: '100%',
        }}>
        {/* Search/filter area styled like the provided image */}
                  <Box sx={{
            display: 'flex',
            flexDirection: { xs: 'column', md: 'row' },
            alignItems: { xs: 'stretch', md: 'center' },
            justifyContent: 'space-between',
            bgcolor: '#f3f4f6',
            p: 2,
            mb: 2,
            borderRadius: 2,
            gap: { xs: 2, md: 0 },
          }}>
          {/* Search box in the right corner */}
          <Box sx={{ minWidth: 320, maxWidth: 400, width: '100%', textAlign: 'right' }}>
            <Typography sx={{ mb: 1, fontWeight: 'bold', color: '#37474f', fontSize: 18 }}>
              رمز / شركة بحث
            </Typography>
            <TextField
              fullWidth
              placeholder="رمز / شركة بحث"
              variant="outlined"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              sx={{ bgcolor: 'white' }}
              inputProps={{ style: { textAlign: 'right' } }}
            />
          </Box>
          {/* Reset button in the left corner */}
          <Box sx={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: { xs: 'flex-start', md: 'flex-end' }, 
            width: { xs: '100%', md: 'auto' }, 
            height: '100%', 
            gap: 4 
          }}>
            {/* Secondary Reset Button */}
            <Button
              variant="text"
              onClick={handleReset}
              sx={{
                minWidth: 150,
                height: 48,
                px: 4,
                py: 2,
                borderRadius: 3,
                bgcolor: '#f8f9fa',
                color: '#6c757d',
                border: '1px solid #e9ecef',
                fontWeight: 500,
                fontSize: 14,
                textTransform: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                transition: 'all 0.2s ease-in-out',
                '&:hover': {
                  bgcolor: '#e9ecef',
                  borderColor: '#dee2e6',
                  transform: 'translateY(-1px)',
                  boxShadow: '0 2px 6px rgba(0,0,0,0.15)',
                },
              }}
            >
              <RefreshIcon sx={{ fontSize: 18, color: '#6c757d' }} />
              إعادة تعيين
            </Button>
            
            {/* Primary Download Button */}
            <Tooltip title="تصدير الجدول إلى Excel" arrow placement="top">
              <Button
                variant="contained"
                onClick={handleExcelExport}
                sx={{
                  minWidth: 150,
                  height: 48,
                  px: 4,
                  py: 2,
                  borderRadius: 3,
                  bgcolor: '#1e6641',
                  color: 'white',
                  fontWeight: 600,
                  fontSize: 14,
                  textTransform: 'none',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  boxShadow: '0 4px 12px rgba(30, 102, 65, 0.3)',
                  transition: 'all 0.2s ease-in-out',
                  '&:hover': {
                    bgcolor: '#14532d',
                    transform: 'translateY(-1px)',
                    boxShadow: '0 6px 16px rgba(30, 102, 65, 0.4)',
                  },
                }}
              >
                <FileDownloadIcon sx={{ fontSize: 18, color: 'white' }} />
                تصدير الجدول
              </Button>
            </Tooltip>

            {/* Unified Update Button */}
            <Tooltip title="تحديث البيانات" arrow placement="top">
              <Button
                variant="outlined"
                onClick={() => setUpdateModalOpen(true)}
                sx={{
                  minWidth: 150,
                  height: 48,
                  px: 3,
                  py: 2,
                  borderRadius: 3,
                  color: '#1e6641',
                  borderColor: '#1e6641',
                  fontWeight: 600,
                  fontSize: 14,
                  textTransform: 'none',
                }}
              >
                تحديث
              </Button>
            </Tooltip>

            {/* Retained-earnings pipeline modal (download → extract → calc → screenshots) */}
            <Modal open={pdfProgressOpen} onClose={() => {}}>
              <Box sx={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: 420, bgcolor: 'background.paper', borderRadius: 2, boxShadow: 24, p: 3, direction: 'rtl' }}>
                <Typography variant="h6" sx={{ fontWeight: 'bold', color: '#1e6641', mb: 0.5 }}>تحديث الأرباح المبقاة قيد التنفيذ</Typography>
                <Typography variant="caption" sx={{ display: 'block', color: '#666', mb: 1.5 }}>
                  تنزيل القوائم من تداول، ثم استخراج القيم والحساب وتحديث اللوحة
                </Typography>
                <LinearProgress color="success" />
                <Typography sx={{ fontSize: 13, color: '#1e6641', mt: 1 }}>
                  الحالة: {pdfJobStatus?.status || 'جاري التنفيذ'} — المُنجز: {pdfJobStatus?.processed || 0}
                  {pdfJobStatus?.current_symbol ? ` — الحالي: ${pdfJobStatus.current_symbol}` : ''}
                </Typography>
                <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
                  <Button variant="outlined" onClick={async () => { try { await fetch(`${API_URL}/api/pdfs/stop`, { method: 'POST' }); } catch (e) {} }} sx={{ color: '#b71c1c', borderColor: '#b71c1c' }}>إيقاف</Button>
                </Box>
              </Box>
            </Modal>

            {/* Update Selection Modal */}
            <Dialog open={updateModalOpen} onClose={() => setUpdateModalOpen(false)}>
              <DialogTitle sx={{ fontWeight: 700, color: '#1e6641' }}>اختر نوع التحديث</DialogTitle>
              <DialogContent sx={{ minWidth: 360 }}>
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                    <input type="checkbox" checked={selectPdf} onChange={(e) => setSelectPdf(e.target.checked)} />
                    تحديث الأرباح المبقاة (تنزيل واستخراج)
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                    <input type="checkbox" checked={selectNet} onChange={(e) => setSelectNet(e.target.checked)} />
                    تحديث صافي الربح
                  </label>
                  <Typography variant="caption" sx={{ color: '#666' }}>
                    يمكن اختيار واحد أو كلاهما. عند اختيار الاثنين: زيارة واحدة لكل شركة على تداول (صافي الربح ثم القوائم)، ثم الاستخراج والحساب.
                  </Typography>
                </Box>
              </DialogContent>
              <DialogActions>
                <Button onClick={() => setUpdateModalOpen(false)} sx={{ color: '#666' }}>إلغاء</Button>
                <Button
                  variant="contained"
                  disabled={!selectPdf && !selectNet}
                  onClick={async () => {
                    setUpdateModalOpen(false);
                    if (selectPdf && !selectNet) {
                      try {
                        const res = await fetch(`${API_URL}/api/run_pdfs_pipeline`, { method: 'POST' });
                        const data = await res.json();
                        if (res.status === 202) {
                          setIsPdfRunning(true);
                          setPdfJobStatus({ status: 'running' });
                          setPdfProgressOpen(true);
                          startPollPdf();
                        } else {
                          alert('❌ لم يتم بدء العملية: ' + (data.message || ''));
                        }
                      } catch (e) {
                        alert('❌ خطأ في الاتصال بالخادم: ' + e.message);
                      }
                    } else if (!selectPdf && selectNet) {
                      try {
                        const res = await fetch(`${API_URL}/api/run_net_profit_scrape`, { method: 'POST' });
                        const data = await res.json();
                        if (res.status === 202) {
                          setIsNetRunning(true);
                          setNetJobStatus({ status: 'running' });
                          setNetProgressOpen(true);
                          startPollNet();
                        } else {
                          alert('❌ لم يتم بدء العملية: ' + (data.message || ''));
                        }
                      } catch (e) {
                        alert('❌ خطأ في الاتصال بالخادم: ' + e.message);
                      }
                    } else {
                      // Both: single combined Playwright pipeline (one company visit), then extract/calc
                      try {
                        const res = await fetch(`${API_URL}/api/run_combined_update`, { method: 'POST' });
                        const data = await res.json();
                        if (res.status === 202) {
                          setPdfJobStatus({ status: 'running', mode: 'combined' });
                          setNetJobStatus({ status: 'running', mode: 'combined' });
                          setBothProgressOpen(true);
                          setBothPdfRunning(true);
                          setBothNetRunning(true);
                          startPollBothCombined();
                        } else {
                          alert('❌ لم يتم بدء التحديث المدمج: ' + (data.message || ''));
                        }
                      } catch (e) {
                        alert('❌ خطأ في الاتصال بالخادم: ' + e.message);
                      } finally {
                        setSelectPdf(false);
                        setSelectNet(false);
                      }
                    }
                  }}
                  sx={{ bgcolor: '#1e6641', '&:hover': { bgcolor: '#14532d' } }}
                >
                  بدء التحديث
                </Button>
              </DialogActions>
            </Dialog>

            {/* Combined Progress Modal for Both */}
            <Modal open={bothProgressOpen} onClose={() => {}}>
              <Box sx={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: 520, bgcolor: 'background.paper', borderRadius: 2, boxShadow: 24, p: 3, direction: 'rtl' }}>
                <Typography variant="h6" sx={{ fontWeight: 'bold', color: '#1e6641', mb: 2 }}>التحديث قيد التنفيذ</Typography>
                <LinearProgress color="success" />
                {/* Finalizing hint */}
                {((pdfJobStatus?.status === 'finalizing') || (netJobStatus?.status === 'finalizing')) && (
                  <Typography sx={{ mt: 1, fontSize: 13, color: '#555' }}>
                    جارٍ الإنهاء: إيقاف العمليات، حساب النتائج، وتحديث اللوحة. يرجى الانتظار حتى يكتمل التحديث.
                  </Typography>
                )}
                <Typography sx={{ fontSize: 13, color: '#1e6641', mt: 1 }}>
                  الأرباح المبقاة: {pdfJobStatus?.status || 'جاري التنفيذ'}{pdfJobStatus?.current_symbol ? ` — ${pdfJobStatus.current_symbol}` : ''}
                </Typography>
                <Typography sx={{ fontSize: 13, color: '#ff9800' }}>
                  صافي الربح: {netJobStatus?.status || 'جاري التنفيذ'}{netJobStatus?.current_symbol ? ` — ${netJobStatus.current_symbol}` : ''}
                </Typography>
                <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
                  <Button
                    variant="outlined"
                    onClick={async () => {
                      setBothIsStopping(true);
                      try { await fetch(`${API_URL}/api/pdfs/stop`, { method: 'POST' }); } catch (e) {}
                      try { await fetch(`${API_URL}/api/net_profit/stop`, { method: 'POST' }); } catch (e) {}
                    }}
                    disabled={bothIsStopping}
                    sx={{ color: bothIsStopping ? '#999' : '#b71c1c', borderColor: bothIsStopping ? '#ccc' : '#b71c1c' }}
                  >
                    {bothIsStopping ? 'جاري الإنهاء...' : 'إيقاف'}
                  </Button>
                </Box>
              </Box>
            </Modal>
            {/* Net Profit Progress Modal */}
            <Modal open={netProgressOpen} onClose={() => {}}>
              <Box sx={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: 420, bgcolor: 'background.paper', borderRadius: 2, boxShadow: 24, p: 3, direction: 'rtl' }}>
                <Typography variant="h6" sx={{ fontWeight: 'bold', color: '#ff9800', mb: 1 }}>تحديث صافي الربح قيد التنفيذ</Typography>
                <LinearProgress color="warning" />
                <Typography sx={{ fontSize: 13, color: '#ff9800', mt: 1 }}>
                  الحالة: {netJobStatus?.status || 'جاري التنفيذ'} — المُنجز: {netJobStatus?.processed || 0}
                  {netJobStatus?.current_symbol ? ` — الحالي: ${netJobStatus.current_symbol}` : ''}
                </Typography>
                <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
                  <Button variant="outlined" onClick={async () => { try { await fetch(`${API_URL}/api/net_profit/stop`, { method: 'POST' }); } catch (e) {} }} sx={{ color: '#b71c1c', borderColor: '#b71c1c' }}>إيقاف</Button>
                </Box>
              </Box>
            </Modal>
          </Box>
        </Box>
        <Box sx={{ display: "flex", gap: 2, alignItems: "center", mb: 2 }}>
            {/* Quarter Filter Dropdown */}
            <TextField
              select
              label="تصفية حسب الربع"
              variant="outlined"
              size="small"
              value={quarterFilter}
              onChange={(e) => setQuarterFilter(e.target.value)}
              sx={{
                minWidth: 200,
                "& .MuiOutlinedInput-root": {
                  borderRadius: 2,
                  "& fieldset": { borderColor: "#e0e0e0" },
                  "&:hover fieldset": { borderColor: "#1e6641" },
                  "&:focus fieldset": { borderColor: "#1e6641" },
                },
                "& .MuiInputLabel-root": { color: "#666" },
                "& .MuiInputLabel-root.Mui-focused": { color: "#1e6641" },
              }}
            >
              <MenuItem value="Q1">الربع الأول (Q1)</MenuItem>
              <MenuItem value="Q2">الربع الثاني (Q2)</MenuItem>
              <MenuItem value="Q3">الربع الثالث (Q3)</MenuItem>
            </TextField>
            
            
          </Box>
        {/* DataGrid */}
        <Box sx={{ 
          width: '100%', 
          overflow: 'auto',
          border: '1px solid #e0e0e0',
          borderRadius: 1,
          maxWidth: '100%',
          p: 0
        }}>
          <DataGrid
            rows={filteredRows}
            columns={dashboardColumns}
            pageSize={20}
            rowsPerPageOptions={[20, 50, 100]}
            disableSelectionOnClick
            autoHeight
            componentsProps={{
              toolbar: {
                showQuickFilter: true,
                quickFilterProps: { debounceMs: 500 },
              },
            }}
            // Optimize column rendering
            columnBuffer={1}
            columnThreshold={1}
            sx={{
              bgcolor: "white",
              fontFamily: "'Tajawal', 'Cairo', 'Noto Sans Arabic', sans-serif",
              direction: "rtl",
              borderRadius: 4,
              fontSize: 18,
              boxShadow: '0 2px 16px 0 rgba(30,102,65,0.08)',
              border: 'none',
              "& .MuiDataGrid-columnHeaders": {
                bgcolor: "#e3ecfa",
                fontWeight: "bold",
                fontSize: 18,
                position: 'sticky',
                top: 0,
                zIndex: 1,
                direction: 'rtl',
                textAlign: 'right',
                boxShadow: '0 2px 8px 0 rgba(30,102,65,0.10)',
                borderTopLeftRadius: 16,
                borderTopRightRadius: 16,
              },
              "& .MuiDataGrid-columnHeader, & .MuiDataGrid-columnHeaderTitle": {
                direction: "rtl",
                textAlign: "right",
                justifyContent: "flex-end",
                paddingRight: "12px !important",
                paddingLeft: "0 !important",
                display: 'flex',
              },
              "& .MuiDataGrid-columnHeaderTitleContainer": {
                flexDirection: "row-reverse",
                direction: 'rtl',
                display: 'flex',
                justifyContent: 'flex-end',
              },
              "& .MuiDataGrid-columnHeaderTitleContainerContent": {
                textAlign: "right",
                justifyContent: "flex-end",
                direction: 'rtl',
                display: 'flex',
              },
              "& .MuiDataGrid-row": {
                minHeight: 44,
                maxHeight: 44,
                transition: 'background 0.2s, box-shadow 0.2s',
                borderRadius: 2,
              },
              "& .MuiDataGrid-row:nth-of-type(even)": { bgcolor: "#f7fafc" },
              "& .MuiDataGrid-row:hover": {
                bgcolor: "#e3f2fd",
                boxShadow: '0 2px 8px 0 rgba(30,102,65,0.08)',
                cursor: 'pointer',
              },
              "& .MuiDataGrid-footerContainer": { 
                bgcolor: '#f4f6fa', 
                fontWeight: 'bold', 
                borderBottomLeftRadius: 16, 
                borderBottomRightRadius: 16 
              },
              "& .MuiDataGrid-virtualScroller": { minHeight: 300 },
              "& .MuiDataGrid-cell": {
                borderBottom: '1px solid #e0e0e0',
                fontWeight: 500,
                fontSize: 17,
                letterSpacing: '0.01em',
                direction: 'rtl',
                textAlign: 'right',
              },
              "& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus": {
                outline: 'none',
              },
              "& .MuiDataGrid-root": {
                borderRadius: 4,
              },
            }}
          />
        </Box>
      </Paper>
      
      {/* Evidence Modal */}
      <EvidenceModal
        open={evidenceModalOpen}
        onClose={() => setEvidenceModalOpen(false)}
        evidenceData={evidenceData}
        loading={evidenceLoading}
        error={evidenceError}
        onDataUpdate={fetchData}
        reportingYear={reportingYear}
      />
      
      {/* Edit Value Modal - Commented out since we're using inline editing */}
      {/* <EditValueModal
        open={editModalOpen}
        onClose={() => setEditModalOpen(false)}
        editData={editData}
        onSave={async (companySymbol, fieldType, newValue, feedback) => {
          setEditLoading(true);
          try {
            const response = await fetch(`${API_URL}/api/correct_retained_earnings`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                company_symbol: companySymbol,
                field_type: fieldType,
                correct_value: newValue,
                feedback: feedback,
              }),
            });
            const data = await response.json();
            if (data.status === 'success' && data.updated) {
              if (typeof globalThis.updateRowAfterCorrection === 'function') {
                globalThis.updateRowAfterCorrection(data.updated);
              }
              setEditModalOpen(false);
              setEditLoading(false);
            } else {
              alert('فشل في حفظ التصحيح: ' + (data.message || ''));
              setEditLoading(false);
            }
          } catch (e) {
            alert('حدث خطأ أثناء حفظ التصحيح: ' + e.message);
            setEditModalOpen(false);
            setEditLoading(false);
          }
        }}
        loading={editLoading}
      /> */}
      
      {/* Footer */}
      <Box sx={{ textAlign: 'center', color: '#888', py: 2, fontSize: 16, mt: 'auto' }}>
        © {new Date().getFullYear()} مركز الابتكار
      </Box>
      {/* Delete confirmation dialog */}
      <Dialog open={deleteDialogOpen} onClose={cancelDeleteExport}>
        <DialogTitle sx={{ fontWeight: 700, color: '#1e6641' }}>تأكيد الحذف</DialogTitle>
        <DialogContent>
          <Typography>هل أنت متأكد أنك تريد حذف هذا الملف؟ لا يمكن التراجع عن هذه العملية.</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={cancelDeleteExport} sx={{ color: '#37474f' }}>إلغاء</Button>
          <Button onClick={confirmDeleteExport} sx={{ color: '#b71c1c', fontWeight: 700 }}>حذف</Button>
        </DialogActions>
      </Dialog>

      {/* Sidebar Drawer */}
      <Drawer
        anchor="left"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        PaperProps={{ sx: { width: 340, bgcolor: '#f8f9fa', borderTopRightRadius: 16, borderBottomRightRadius: 16 } }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 3,
            py: 2.5,
            bgcolor: '#fff',
            borderBottom: '1.5px solid #e0e0e0',
            boxShadow: '0 2px 8px 0 rgba(30,102,65,0.04)',
            borderTopRightRadius: 16,
            borderTopLeftRadius: 16,
            minHeight: 72,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', justifyContent: 'space-between' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.2 }}>
              <FolderOpenIcon sx={{ color: '#1e6641', fontSize: 24, mb: '2px' }} />
              <Typography variant="h5" sx={{ fontWeight: 800, color: '#1e6641', fontSize: 20, lineHeight: 1.2 }}>
                الملفات المحفوظة
              </Typography>
            </Box>
            <IconButton
              aria-label="إغلاق القائمة الجانبية"
              onClick={() => setDrawerOpen(false)}
              sx={{
                color: '#1e6641',
                bgcolor: '#f4f6fa',
                borderRadius: '50%',
                boxShadow: '0 2px 8px 0 rgba(30,102,65,0.10)',
                p: 0.5,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'background 0.2s, color 0.2s, box-shadow 0.2s',
                '&:hover': { bgcolor: '#e3ecfa', color: '#14532d', boxShadow: '0 4px 16px 0 rgba(30,102,65,0.18)' },
              }}
            >
              <ArrowBackIosNewIcon sx={{ fontSize: 18, transform: 'scaleX(-1)' }} />
            </IconButton>
          </Box>
        </Box>
        {/* Soft divider and extra space below header */}
        <Box sx={{ height: 18 }} />
        <Box sx={{ width: '100%', height: 2, bgcolor: '#f4f6fa', mb: 2, borderRadius: 2 }} />
        {/* User-Saved Exports Section */}
        <Box sx={{ mt: 2, pb: 0, px: 0 }}>
          <Box sx={{
            display: 'flex',
            alignItems: 'center',
            bgcolor: '#e9f5ee',
            borderRadius: 4,
            px: 2,
            py: 1.2,
            width: '100%',
            boxSizing: 'border-box',
            mb: 1.5,
            gap: 1.5,
          }}>
            <Box sx={{ width: 3, height: 24, bgcolor: '#1e6641', borderRadius: 6, mr: 0 }} />
            <Typography
              variant="subtitle1"
              sx={{
                fontWeight: 700,
                color: '#1e6641',
                fontSize: 18,
                letterSpacing: 0.1,
                minWidth: 0,
                pr: 1,
              }}
            >
              ملفاتك المصدّرة
            </Typography>
          </Box>
        </Box>
        <List>
          {(() => {
            if (userExportsLoading) {
              return (
                <ListItem sx={{ justifyContent: 'center' }}><CircularProgress size={22} sx={{ color: '#1e6641' }} /></ListItem>
              );
            }
            if (userExportsError) {
              return (
                <ListItem><Alert severity="error">{userExportsError}</Alert></ListItem>
              );
            }
            if (userExports.length === 0) {
              return (
            <ListItem sx={{ justifyContent: 'center', alignItems: 'center', minHeight: 80, width: '100%' }}>
              <Typography sx={{ color: '#b0b7be', fontSize: 17, textAlign: 'center', width: '100%' }}>
                لا توجد ملفات محفوظة بعد
              </Typography>
            </ListItem>
              );
            }
            return userExports.map((file) => (
              <ListItem
                key={file.filename || file.download_url}
                tabIndex={0}
                sx={{
                  pl: 3, pr: 3, py: 2.2,
                  mb: 1.5,
                  bgcolor: '#fff',
                  borderRadius: 2.5,
                  boxShadow: '0 1px 6px 0 rgba(30,102,65,0.06)',
                  display: 'flex',
                  alignItems: 'center',
                  '&:hover .export-delete-btn': { opacity: 1 },
                  minHeight: 56,
                  border: 'none',
                  transition: 'box-shadow 0.2s, transform 0.2s',
                  '&:hover': {
                    boxShadow: '0 4px 16px 0 rgba(30,102,65,0.10)',
                    transform: 'translateY(-2px) scale(1.01)',
                  },
                  outline: 'none',
                  '&:focus': {
                    boxShadow: '0 0 0 2px #1e664144',
                  },
                }}
              >
                <Box sx={{ flexGrow: 1 }}>
                  <Typography sx={{ fontWeight: 500, color: '#1e6641', fontSize: 15 }}>
                    {(() => {
                      // file.export_date is 'YYYY-MM-DD HH:mm:ss'
                      const datePart = file.export_date.split(' ')[0];
                      const [year, month, day] = datePart.split('-');
                      return `dashboard-${day}-${month}-${year}`;
                    })()}
                  </Typography>
                </Box>
                <Tooltip title="تحميل" arrow>
                  <IconButton
                    aria-label="تحميل"
                    href={`${API_URL}${file.download_url}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    sx={{
                      color: '#1e6641',
                      bgcolor: 'transparent',
                      borderRadius: '50%',
                      p: 0.7,
                      mx: 0.5,
                      transition: 'color 0.2s, background 0.2s',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 22,
                      height: 36,
                      width: 36,
                      minWidth: 36,
                    }}
                  >
                    <DownloadIcon sx={{ fontSize: 22 }} />
                  </IconButton>
                </Tooltip>
                <Tooltip title="حذف" arrow>
                  <IconButton
                    aria-label="حذف"
                    className="export-delete-btn"
                    onClick={() => handleDeleteExport(file)}
                    sx={{
                      ml: 0.5,
                      opacity: 0,
                      color: '#7b7b7b',
                      bgcolor: 'transparent',
                      borderRadius: '50%',
                      p: 0.7,
                      transition: 'opacity 0.2s, color 0.2s, background 0.2s',
                      boxShadow: 'none',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 22,
                      height: 36,
                      width: 36,
                      minWidth: 36,
                      '&:hover': { color: '#444', bgcolor: 'rgba(120,120,120,0.07)' },
                    }}
                    size="small"
                  >
                    <DeleteOutlineIcon sx={{ fontSize: 22 }} />
                  </IconButton>
                </Tooltip>
              </ListItem>
            ));
          })()}
        </List>
        {/* Divider between sections */}
        <Box sx={{ mt: 2, pb: 0, px: 0 }}>
          <Box sx={{
            display: 'flex',
            alignItems: 'center',
            bgcolor: '#e9f5ee',
            borderRadius: 4,
            px: 2,
            py: 1.2,
            width: '100%',
            boxSizing: 'border-box',
            mb: 1.5,
            gap: 1.5,
          }}>
            <Box sx={{ width: 3, height: 24, bgcolor: '#1e6641', borderRadius: 6, mr: 0 }} />
            <Typography
              variant="subtitle1"
              sx={{
                fontWeight: 700,
                color: '#1e6641',
                fontSize: 18,
                letterSpacing: 0.1,
                minWidth: 0,
                pr: 1,
              }}
            >
              أرشيف الفترات الربعية
            </Typography>
          </Box>
        </Box>
        
        {/* Custom Export Section - Right next to Quarterly Archives */}
        <Box sx={{ px: 2, mb: 2 }}>
          <Tooltip 
            title="تصدير البيانات المالية لتاريخ محدد أو نطاق زمني معين مع إمكانية تخصيص البيانات المطلوبة"
            arrow
            placement="top"
          >
            <Box sx={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              bgcolor: '#f8f9fa',
              borderRadius: 3,
              px: 2,
              py: 1.5,
              border: '1px solid #e9ecef',
              cursor: 'pointer',
              transition: 'all 0.3s ease-in-out',
              '&:hover': {
                bgcolor: '#e9ecef',
                borderColor: '#1e6641',
                transform: 'translateY(-1px)',
                boxShadow: '0 4px 12px rgba(30, 102, 65, 0.15)',
              },
              '&:active': {
                transform: 'translateY(0px)',
              }
            }}
            onClick={() => setCustomExportExpanded(!customExportExpanded)}
            >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box sx={{ 
                width: 20, 
                height: 20, 
                bgcolor: '#1e6641', 
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontSize: 12,
                fontWeight: 'bold',
                transition: 'all 0.3s ease-in-out',
                animation: customExportExpanded ? 'pulse 2s infinite' : 'none',
                '@keyframes pulse': {
                  '0%': { transform: 'scale(1)' },
                  '50%': { transform: 'scale(1.1)' },
                  '100%': { transform: 'scale(1)' },
                }
              }}>
              </Box>
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  color: '#495057',
                  fontSize: 14,
                  transition: 'color 0.3s ease-in-out',
                }}
              >
                تصدير مخصص
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: '#6c757d',
                  fontSize: 11,
                  opacity: 0.8,
                  display: 'block',
                  lineHeight: 1.2
                }}
              >
                تصدير البيانات لتاريخ محدد
              </Typography>
            </Box>
            <IconButton
              size="small"
              sx={{
                color: '#1e6641',
                p: 0.5,
                transition: 'all 0.3s ease-in-out',
                transform: customExportExpanded ? 'rotate(45deg)' : 'rotate(0deg)',
                '&:hover': { 
                  color: '#14532d',
                  bgcolor: 'rgba(30, 102, 65, 0.1)',
                  transform: customExportExpanded ? 'rotate(45deg) scale(1.1)' : 'scale(1.1)',
                },
              }}
            >
              <Add />
            </IconButton>
          </Box>
          </Tooltip>
        </Box>
        
        {/* Collapsible Custom Export Controls */}
        <Modal 
          open={customExportExpanded} 
          onClose={() => setCustomExportExpanded(false)}
          closeAfterTransition
        >
          <Fade in={customExportExpanded}>
            <Box sx={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: { xs: '90vw', sm: 480 },
              bgcolor: 'background.paper',
              borderRadius: 4,
              boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
              p: 0,
              outline: 'none',
              direction: 'rtl'
            }}>
              {/* Header */}
              <Box sx={{
                bgcolor: 'linear-gradient(135deg, #1e6641 0%, #2d7a4a 100%)',
                color: 'white',
                px: 3,
                py: 2.5,
                borderRadius: '16px 16px 0 0',
                position: 'relative'
              }}>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Box sx={{
                      width: 40,
                      height: 40,
                      bgcolor: 'rgba(255,255,255,0.2)',
                      borderRadius: '50%',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 18,
                      fontWeight: 'bold'
                    }}>
                      E
                    </Box>
                    <Box>
                      <Typography variant="h6" sx={{ fontWeight: 'bold', fontSize: 16, mb: 0.5 }}>
                        التصدير المخصص
                      </Typography>
                      <Typography variant="body2" sx={{ opacity: 0.9, fontSize: 12 }}>
                        تصدير البيانات لتاريخ معين
                      </Typography>
                    </Box>
                  </Box>
                  <IconButton
                    onClick={() => setCustomExportExpanded(false)}
                    sx={{
                      color: 'white',
                      bgcolor: 'rgba(255,255,255,0.1)',
                      '&:hover': { bgcolor: 'rgba(255,255,255,0.2)' }
                    }}
                    size="small"
                  >
                    ✕
                  </IconButton>
                </Box>
              </Box>

              {/* Content */}
              <Box sx={{ p: 3 }}>
                {/* Date Selection */}
                <Box sx={{ mb: 3 }}>
                  <Typography variant="subtitle2" sx={{ 
                    fontWeight: 600, 
                    color: '#1e6641', 
                    mb: 1.5, 
                    fontSize: 14,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1
                  }}>
                    تاريخ التصدير
                  </Typography>
                  
                  <TextField
                    type="date"
                    fullWidth
                    value={customExportDate}
                    onChange={(e) => setCustomExportDate(e.target.value)}
                    sx={{
                      "& .MuiOutlinedInput-root": {
                        borderRadius: 3,
                        fontSize: 14,
                        "& fieldset": { 
                          borderColor: "#e0e0e0",
                          borderWidth: 2
                        },
                        "&:hover fieldset": { 
                          borderColor: "#1e6641",
                          borderWidth: 2
                        },
                        "&:focus fieldset": { 
                          borderColor: "#1e6641",
                          borderWidth: 2
                        },
                      },
                      "& .MuiInputLabel-root": { 
                        color: "#666", 
                        fontSize: 13,
                        fontWeight: 500
                      },
                    }}
                    size="medium"
                  />
                  
                  {/* Quarter Preview */}
                  {customExportDate && (
                    <Box sx={{ 
                      mt: 2,
                      p: 3,
                      bgcolor: 'linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%)',
                      borderRadius: 3,
                      border: '2px solid #0ea5e9',
                      animation: 'fadeInUp 0.4s ease-out'
                    }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1 }}>
                        <Box sx={{
                          width: 32,
                          height: 32,
                          bgcolor: '#0ea5e9',
                          borderRadius: '50%',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          color: 'white',
                          fontSize: 14,
                          fontWeight: 'bold'
                        }}>
                          Q
                        </Box>
                        <Typography variant="subtitle2" sx={{ 
                          color: '#0369a1', 
                          fontWeight: 'bold',
                          fontSize: 14
                        }}>
                          الربع المحتوي على التاريخ
                        </Typography>
                      </Box>
                      <Typography variant="h6" sx={{ 
                        color: '#0369a1', 
                        fontWeight: 'bold',
                        fontSize: 18,
                        textAlign: 'center'
                      }}>
                        {getQuarterFromDate(customExportDate)}
                      </Typography>
                    </Box>
                  )}
                </Box>

                {/* File Name */}
                <Box sx={{ mb: 3 }}>
                  <Typography variant="subtitle2" sx={{ 
                   fontWeight: 600, 
                    color: '#1e6641', 
                    mb: 1.5, 
                    fontSize: 14,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1
                  }}>
                    اسم الملف المخصص (اختياري)
                  </Typography>
                  
                  <TextField
                    fullWidth
                    placeholder="مثال: تقرير_ديسمبر_2024"
                    value={customFileName}
                    onChange={(e) => setCustomFileName(e.target.value)}
                    sx={{
                      "& .MuiOutlinedInput-root": {
                        borderRadius: 3,
                        fontSize: 14,
                        "& fieldset": { 
                          borderColor: "#e0e0e0",
                          borderWidth: 2
                        },
                        "&:hover fieldset": { 
                          borderColor: "#1e6641",
                          borderWidth: 2
                        },
                        "&:focus fieldset": { 
                          borderColor: "#1e6641",
                          borderWidth: 2
                        },
                      },
                      "& .MuiInputLabel-root": { 
                        color: "#666", 
                        fontSize: 13,
                        fontWeight: 500
                      },
                    }}
                    size="medium"
                  />
                  <Typography variant="caption" sx={{ 
                    color: '#666', 
                    fontSize: 11, 
                    mt: 0.5, 
                    display: 'block',
                    fontStyle: 'italic'
                  }}>
                    سيتم إضافة التاريخ والوقت تلقائياً إذا لم تحدد اسماً مخصصاً
                  </Typography>
                </Box>

                {/* Action Buttons */}
                <Box sx={{ 
                  display: 'flex', 
                  gap: 2, 
                  justifyContent: 'flex-end',
                  mt: 3
                }}>
                  <Button
                    variant="outlined"
                    onClick={() => setCustomExportExpanded(false)}
                    sx={{
                      borderRadius: 3,
                      px: 3,
                      py: 1.2,
                      borderColor: '#e0e0e0',
                      color: '#666',
                      fontWeight: 600,
                      fontSize: 13,
                      textTransform: 'none',
                      '&:hover': {
                        borderColor: '#d0d0d0',
                        bgcolor: '#f8f8f8'
                      }
                    }}
                  >
                    إلغاء
                  </Button>
                  
                  <Button
                    variant="contained"
                    onClick={handleCustomDateExport}
                    disabled={!customExportDate}
                    sx={{
                      borderRadius: 3,
                      px: 4,
                      py: 1.2,
                      bgcolor: customExportDate ? '#1e6641' : '#ccc',
                      color: 'white',
                      fontWeight: 'bold',
                      fontSize: 14,
                      textTransform: 'none',
                      minWidth: 140,
                      transition: 'all 0.3s ease-out',
                      '&:hover': {
                        bgcolor: customExportDate ? '#14532d' : '#ccc',
                        transform: customExportDate ? 'translateY(-2px)' : 'none',
                        boxShadow: customExportDate ? '0 8px 20px rgba(30, 102, 65, 0.3)' : 'none',
                      },
                      '&:disabled': {
                        bgcolor: '#ccc',
                        cursor: 'not-allowed',
                        transform: 'none'
                      }
                    }}
                    startIcon={<FileDownloadIcon />}
                  >
                    {customExportDate ? 'تصدير' : 'اختر تاريخ أولاً'}
                  </Button>
                </Box>
              </Box>

              {/* Loading Overlay */}
              {loading && (
                <Box sx={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  bgcolor: 'rgba(255,255,255,0.8)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: 4
                }}>
                  <Box sx={{ textAlign: 'center' }}>
                    <CircularProgress size={40} sx={{ color: '#1e6641', mb: 2 }} />
                    <Typography variant="body2" sx={{ color: '#1e6641', fontWeight: 600 }}>
                      جاري التصدير...
                    </Typography>
                  </Box>
                </Box>
              )}
            </Box>
          </Fade>
        </Modal>

        {/* Keep the old collapse structure for backward compatibility, but hide it */}
        <Collapse in={false}>
          <Box sx={{ 
            px: 2, 
            mb: 2,
            bgcolor: '#fafbfc',
            borderRadius: 3,
            border: '1px solid #e9ecef',
            py: 2
          }}>
            <Typography variant="body2" sx={{ color: '#495057', mb: 1.5, fontSize: 13, fontWeight: 600 }}>
              📅 تصدير لتاريخ مخصص
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
              <TextField
                type="date"
                size="small"
                value={customExportDate}
                onChange={(e) => setCustomExportDate(e.target.value)}
                sx={{
                  "& .MuiOutlinedInput-root": {
                    borderRadius: 2,
                    fontSize: 13,
                    "& fieldset": { borderColor: "#e0e0e0" },
                    "&:hover fieldset": { borderColor: "#1e6641" },
                    "&:focus fieldset": { borderColor: "#1e6641" },
                  },
                  "& .MuiInputLabel-root": { color: "#666", fontSize: 12 },
                }}
              />
              
              {/* Show quarter mapping for custom date */}
              {customExportDate && (
                <Box sx={{ 
                  display: 'flex', 
                  alignItems: 'center', 
                  gap: 1, 
                  px: 1.5, 
                  py: 0.8, 
                  bgcolor: '#e8f5e8', 
                  borderRadius: 2,
                  border: '1px solid #c3e6c3',
                  animation: 'fadeIn 0.3s ease-in-out'
                }}>
                  <Typography variant="caption" sx={{ color: '#1e6641', fontWeight: 600, fontSize: 11 }}>
                    🎯 الربع: {getQuarterFromDate(customExportDate)}
                  </Typography>
                </Box>
              )}
              
              <Button
                variant="contained"
                onClick={handleCustomDateExport}
                disabled={!customExportDate}
                size="small"
                sx={{
                  borderRadius: 2,
                  bgcolor: customExportDate ? '#1e6641' : '#ccc',
                  color: 'white',
                  fontWeight: 600,
                  fontSize: 12,
                  textTransform: 'none',
                  py: 1,
                  transition: 'all 0.2s ease-in-out',
                  '&:hover': {
                    bgcolor: customExportDate ? '#14532d' : '#ccc',
                    transform: customExportDate ? 'translateY(-1px)' : 'none',
                    boxShadow: customExportDate ? '0 4px 8px rgba(30, 102, 65, 0.3)' : 'none',
                  },
                  '&:disabled': {
                    bgcolor: '#ccc',
                    cursor: 'not-allowed',
                  }
                }}
                startIcon={customExportDate ? <FileDownloadIcon /> : null}
              >
                {customExportDate ? '📥 تصدير للتاريخ المحدد' : 'اختر تاريخ أولاً'}
              </Button>
            </Box>
          </Box>
          
          {/* File Naming Customization */}
          <Box sx={{ 
            px: 2, 
            mb: 2,
            bgcolor: '#fafbfc',
            borderRadius: 3,
            border: '1px solid #e9ecef',
            py: 2
          }}>
            <Typography variant="body2" sx={{ color: '#495057', mb: 1.5, fontSize: 13, fontWeight: 600 }}>
              📝 تخصيص اسم الملف
            </Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
              <TextField
                size="small"
                placeholder="اسم مخصص للملف (اختياري)"
                value={customFileName}
                onChange={(e) => setCustomFileName(e.target.value)}
                sx={{
                  "& .MuiOutlinedInput-root": {
                    borderRadius: 2,
                    fontSize: 13,
                    "& fieldset": { borderColor: "#e0e0e0" },
                    "&:hover fieldset": { borderColor: "#1e6641" },
                    "&:focus fieldset": { borderColor: "#1e6641" },
                  },
                  "& .MuiInputLabel-root": { color: "#666", fontSize: 12 },
                }}
              />
              <Typography variant="caption" sx={{ color: '#888', fontSize: 10, fontStyle: 'italic' }}>
                💡 سيتم إضافة التاريخ والوقت تلقائياً
              </Typography>
            </Box>
          </Box>
        </Collapse>
        
        {/* Quarterly Archives List */}
        <List>
          {(() => {
            if (snapshotsLoading) {
              return (
                <ListItem sx={{ justifyContent: 'center' }}><CircularProgress size={22} sx={{ color: '#1e6641' }} /></ListItem>
              );
            }
            if (snapshotsError) {
              return (
                <ListItem><Alert severity="error">{snapshotsError}</Alert></ListItem>
              );
            }
            if (snapshots.length === 0) {
              return (
                <ListItem sx={{ justifyContent: 'center', color: '#888' }}>لا توجد ملفات محفوظة بعد</ListItem>
              );
            }
            return snapshots.map((snap) => (
              <ListItem
                key={snap.download_url || `${snap.year}-${snap.quarter}-${snap.snapshot_date}`}
                sx={{ pl: 2, pr: 2, py: 1, borderBottom: '1px solid #e0e0e0', display: 'flex', alignItems: 'center' }}
              >
                <Typography sx={{ fontWeight: 500, color: '#1e6641', flexGrow: 1, fontSize: 16 }}>
                  {`${snap.year} ${snap.quarter} — ${snap.snapshot_date}`}
                </Typography>
                <Tooltip title={`تاريخ الاستخراج: ${snap.snapshot_date}`} arrow>
                  <Button
                    variant="contained"
                    color="success"
                    size="small"
                    href={`${API_URL}${snap.download_url}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    sx={{ minWidth: 0, px: 2, py: 1, borderRadius: 2, fontWeight: 600 }}
                    startIcon={<DownloadIcon />}
                  >
                    تحميل
                  </Button>
                </Tooltip>
              </ListItem>
            ));
          })()}
        </List>
      </Drawer>
    </Box>
  );
}

export default App;
