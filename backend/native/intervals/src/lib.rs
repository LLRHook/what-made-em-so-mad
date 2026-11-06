//! Greedy interval matching for offline evaluation. The caller supplies predictions
//! in stable descending confidence order; all indices refer to these input slices.

use std::{mem, panic, slice};

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct Interval {
    pub start: f64,
    pub end: f64,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Match {
    pub truth_index: usize,
    pub prediction_index: usize,
}

// Python min/max keep the first operand on ties and unordered comparisons.
// f64::min/max would change the legacy behavior for NaN and signed zero.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn iou(a: Interval, b: Interval) -> f64 {
    let overlap = py_max(0.0, py_min(a.end, b.end) - py_max(a.start, b.start));
    let union = py_max(a.end, b.end) - py_min(a.start, b.start);
    if union <= 0.0 {
        0.0
    } else {
        overlap / union
    }
}

fn match_segments(
    truth: &[Interval],
    predictions: &[Interval],
    min_iou: f64,
    out: &mut [Match],
) -> usize {
    let mut available = vec![true; truth.len()];
    let mut count = 0;
    for (prediction_index, &prediction) in predictions.iter().enumerate() {
        let mut best_index = None;
        let mut best_iou = 0.0;
        // Strict improvement retains the first available truth on an IoU tie.
        for (index, &actual) in truth.iter().enumerate() {
            if available[index] {
                let score = iou(actual, prediction);
                if score > best_iou {
                    best_iou = score;
                    best_index = Some(index);
                }
            }
        }
        if let Some(index) = best_index {
            if best_iou >= min_iou {
                available[index] = false;
                out[count] = Match {
                    truth_index: index,
                    prediction_index,
                };
                count += 1;
            }
        }
    }
    count
}

#[no_mangle]
pub extern "C" fn wmesm_intervals_abi_version() -> u32 {
    1
}

fn valid_buffer<T>(ptr: *const T, len: usize) -> bool {
    len == 0
        || (!ptr.is_null() && ptr.is_aligned() && len <= isize::MAX as usize / mem::size_of::<T>())
}

/// Return the number of matches, -1 for invalid buffer shapes, or -2 on panic.
/// Empty inputs need no buffers. Output capacity must be at least min(input sizes).
/// No Rust allocation or pointer escapes the call.
///
/// # Safety
/// Nonempty buffers must be aligned, live allocations of the declared lengths.
/// Output must be writable and must not overlap either input. The caller must not
/// mutate inputs or access output while this function is running.
#[no_mangle]
pub unsafe extern "C" fn wmesm_match_intervals_v1(
    truth: *const Interval,
    truth_len: usize,
    predictions: *const Interval,
    predictions_len: usize,
    min_iou: f64,
    out: *mut Match,
    capacity: usize,
) -> isize {
    let required = truth_len.min(predictions_len);
    if !valid_buffer(truth, truth_len)
        || !valid_buffer(predictions, predictions_len)
        || capacity < required
        || !valid_buffer(out, required)
    {
        return -1;
    }
    if required == 0 {
        return 0;
    }
    panic::catch_unwind(|| {
        // SAFETY: shapes were checked above; live, disjoint allocations are the
        // caller's obligation. required > 0 implies both input sizes are nonzero.
        let (truth, predictions, out) = unsafe {
            (
                slice::from_raw_parts(truth, truth_len),
                slice::from_raw_parts(predictions, predictions_len),
                slice::from_raw_parts_mut(out, required),
            )
        };
        match_segments(truth, predictions, min_iou, out) as isize
    })
    .unwrap_or(-2)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ptr;

    fn interval(start: f64, end: f64) -> Interval {
        Interval { start, end }
    }

    #[test]
    fn greedy_order_and_truth_ties() {
        let truth = [interval(0.0, 10.0), interval(0.0, 10.0)];
        let predictions = [interval(0.0, 10.0); 3];
        let mut out = [Match::default(); 2];
        assert_eq!(match_segments(&truth, &predictions, 0.5, &mut out), 2);
        assert_eq!(
            out,
            [
                Match {
                    truth_index: 0,
                    prediction_index: 0
                },
                Match {
                    truth_index: 1,
                    prediction_index: 1
                }
            ]
        );
    }

    #[test]
    fn threshold_is_inclusive_but_zero_overlap_never_matches() {
        let truth = [interval(0.0, 10.0)];
        let mut out = [Match::default()];
        assert_eq!(
            match_segments(&truth, &[interval(0.0, 5.0)], 0.5, &mut out),
            1
        );
        assert_eq!(
            match_segments(&truth, &[interval(10.0, 20.0)], 0.0, &mut out),
            0
        );
        assert_eq!(
            match_segments(&truth, &[interval(0.0, 5.0)], 0.5000000000000001, &mut out),
            0
        );
    }

    #[test]
    fn python_nan_operand_order() {
        assert_eq!(iou(interval(0.0, 10.0), interval(f64::NAN, 10.0)), 1.0);
        assert!(iou(interval(f64::NAN, 10.0), interval(0.0, 10.0)).is_nan());
        assert_eq!(py_min(-0.0, 0.0).to_bits(), (-0.0_f64).to_bits());
    }

    #[test]
    fn ffi_checks_buffers_before_dereferencing() {
        let actual = interval(0.0, 10.0);
        let mut out = Match::default();
        // SAFETY: valid buffers, except explicitly rejected null/shape inputs.
        unsafe {
            assert_eq!(
                wmesm_match_intervals_v1(ptr::null(), 0, ptr::null(), 0, 0.5, ptr::null_mut(), 0),
                0
            );
            assert_eq!(
                wmesm_match_intervals_v1(ptr::null(), 1, &actual, 1, 0.5, &mut out, 1),
                -1
            );
            assert_eq!(
                wmesm_match_intervals_v1(&actual, 1, &actual, 1, 0.5, &mut out, 0),
                -1
            );
            assert_eq!(
                wmesm_match_intervals_v1(&actual, 1, &actual, 1, 0.5, ptr::null_mut(), 1),
                -1
            );
            assert_eq!(
                wmesm_match_intervals_v1(&actual, usize::MAX, &actual, 1, 0.5, &mut out, 1),
                -1
            );
            assert_eq!(
                wmesm_match_intervals_v1(&actual, 1, &actual, 1, 0.5, &mut out, 1),
                1
            );
        }
        assert_eq!(
            out,
            Match {
                truth_index: 0,
                prediction_index: 0
            }
        );
    }
}
