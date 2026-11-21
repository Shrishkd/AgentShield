(module
  ;; Each bit represents one synthetic source ID or term. Zero citations
  ;; are allowed for an original answer, but cannot repair an invalid citation.
  (func $supported (param $used i32) (param $allowed i32) (result i32)
    (i32.eq (i32.and (local.get $used) (local.get $allowed)) (local.get $used)))
  (func (export "citation_retry") (param $enabled i32) (param $allowed i32)
        (param $draft i32) (param $retry i32) (result i32)
    (if (result i32) (i32.or (i32.eqz (local.get $enabled))
                             (call $supported (local.get $draft) (local.get $allowed)))
      (then (i32.const 0))
      (else (if (result i32) (i32.and (i32.ne (local.get $retry) (i32.const 0))
                                      (call $supported (local.get $retry) (local.get $allowed)))
              (then (i32.const 1)) (else (i32.const 2))))))
  (func (export "source_fallback") (param $enabled i32) (param $allowed i32)
        (param $draft i32) (param $retry i32) (result i32)
    (if (result i32) (i32.and (local.get $enabled)
                              (i32.eqz (call $supported (local.get $draft) (local.get $allowed))))
      (then (i32.const 3)) (else (i32.const 0)))))
