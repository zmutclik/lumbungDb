-- Akun untuk mengubah tuning tanpa restart. Hanya bisa memanggil procedure di bawah, yang hanya menerima variabel
-- tuning dan nilai angka; SET GLOBAL lain dan isi tabel tidak bisa. Masuk lewat socket sebagai user OS lumbung_tuning,
-- yang hanya dipakai lumbung-helper (root). Volume baru: dijalankan init-backup-user.sh. Volume lama: jalankan sekali
-- sebagai root (lihat README).
CREATE USER IF NOT EXISTS 'lumbung_tuning'@'localhost' IDENTIFIED VIA unix_socket;
DELIMITER //
CREATE OR REPLACE DEFINER='root'@'localhost' PROCEDURE mysql.lumbung_set_global(IN var_name VARCHAR(64), IN new_value BIGINT UNSIGNED)
    SQL SECURITY DEFINER
BEGIN
    IF var_name NOT IN ('innodb_buffer_pool_size', 'innodb_log_file_size', 'innodb_read_io_threads',
                        'innodb_write_io_threads', 'innodb_io_capacity', 'innodb_io_capacity_max',
                        'innodb_flush_neighbors', 'max_connections', 'thread_cache_size', 'tmp_table_size',
                        'max_heap_table_size', 'table_open_cache', 'table_definition_cache') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'variabel tidak diizinkan';
    END IF;
    SET @lumbung_set_global = CONCAT('SET GLOBAL ', var_name, ' = ', new_value);
    PREPARE lumbung_stmt FROM @lumbung_set_global;
    EXECUTE lumbung_stmt;
    DEALLOCATE PREPARE lumbung_stmt;
END//
DELIMITER ;
GRANT EXECUTE ON PROCEDURE mysql.lumbung_set_global TO 'lumbung_tuning'@'localhost';
FLUSH PRIVILEGES;
