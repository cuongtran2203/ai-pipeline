# Báo cáo dữ liệu ds-v1.1

**Synthetic-only, không đảm bảo production.** Nguồn ds-v1 (100-sync-hw-v1), split-v1 theo trang.

## Tổng quan

100 trang, 504 dòng. Có 477 ngày nhìn thấy và crop whole-cell đã tạo lại.

## Nội dung chi tiết

| Split | Trang | Dòng | HW crop | Print crop | Whole-date crop |
|---|---:|---:|---:|---:|---:|
| train | 57 | 291 | 1936 | 883 | 275 |
| val | 14 | 71 | 465 | 212 | 65 |
| test | 29 | 142 | 953 | 439 | 137 |

Nhãn date p0/p1/p2 từ visible.date: {'p0': 477, 'p1': 477, 'p2': 477}. Nhãn whole-cell cũ khác chuỗi có dấu phân cách ở 477/477 crop. Crop mới dùng bbox date và chặn trước bbox work_code, bỏ ô dư ở mép phải.

Phân bố break_min (phút, null giữ nguyên): {'30': 60, '45': 55, '60': 310, '90': 49, 'None': 30}.

Phân bố suy hao ảnh theo trang: train={'light': 16, 'medium': 10, 'phone': 31}; val={'light': 3, 'medium': 3, 'phone': 8}; test={'light': 8, 'medium': 5, 'phone': 16}.

Phân bố 14 profile theo trang: train={'border_touch': 4, 'break_under': 4, 'ends_apart': 3, 'heavy_shift': 4, 'merged_cell': 6, 'merged_tight': 4, 'no_startend_row': 4, 'pen_var': 4, 'start_middle': 4, 'start_right': 4, 'tilde_overlap': 4, 'tilde_touch': 5, 'vshift_pair': 4, 'wide_spread': 3}; val={'border_touch': 1, 'break_under': 1, 'ends_apart': 1, 'heavy_shift': 1, 'merged_cell': 1, 'merged_tight': 1, 'no_startend_row': 1, 'pen_var': 1, 'start_middle': 1, 'start_right': 1, 'tilde_overlap': 1, 'tilde_touch': 1, 'vshift_pair': 1, 'wide_spread': 1}; test={'border_touch': 2, 'break_under': 2, 'ends_apart': 3, 'heavy_shift': 2, 'merged_cell': 1, 'merged_tight': 3, 'no_startend_row': 2, 'pen_var': 2, 'start_middle': 2, 'start_right': 2, 'tilde_overlap': 2, 'tilde_touch': 1, 'vshift_pair': 2, 'wide_spread': 3}.

Train/val/test có file nhãn riêng; tập train chỉ tham chiếu trang train. Script kiểm tra trang không trùng giữa các split và fold, nhãn HW khớp visible, break từ visible khớp GT sau chuẩn hóa. Không tạo từ vựng từ val/test.

SHA-256 nội dung ds-v1.1: `36eec60fa378c4d31c832a9e8ea7d98c10721a55e14ee0253b0cb882f6d2a476`. SHA-256 split-v1: `ed7112f2fe27b56ebbbe25b8b77787b359fff9be6660211b04495df2d291ce2f`.

## Kết luận

Bộ ds-v1.1 tái lập được từ ds-v1 + split-v1; các số đếm và hash chỉ xác nhận tính nhất quán của dữ liệu synthetic, không đo chất lượng OCR trên ảnh thật.
