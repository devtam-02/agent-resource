# Cách viết ưu tiên

Khi sửa hoặc viết mới, đưa code về các dạng dưới đây. Không refactor style hàng loạt ngoài phạm vi task.

## Cấu trúc: hexagonal (ports & adapters)
```
adapter/in/web            controller (+ interface *Api chứa annotation OpenAPI)
adapter/in/messaging      Kafka listener
adapter/out/persistence   entity/ (*Entity), repository/ (*Repository), *Adapter
adapter/out/external      *FeignClient / WebClient, *Dto của hệ thống ngoài, *Adapter
application/port/in       *UseCase
application/port/out      *Port / *Store
application/service       hiện thực use case
application/dto           *Command, *Query, *Response
domain/model, domain/exception
```
- `domain` không import Spring, JPA, web. `application` chỉ đi ra ngoài qua port.
- Record nhỏ gắn với port có thể lồng trong interface port.
- Controller mỏng: validate, gọi use case, map response. Không có logic nghiệp vụ.

## Java
- **Constructor injection**: field `final` + `@RequiredArgsConstructor`; constructor tường minh khi cần `@Qualifier`/`@Value`. Không `@Autowired` field, không `@Value` trên field.
- **Record** cho DTO, command/query, value object, response. Không `@Data`. Entity JPA dùng `@Getter @Setter @Builder @NoArgsConstructor @AllArgsConstructor` (không `@Data` trên entity: equals/hashCode/toString với lazy relation).
- `Optional` cho kết quả có thể vắng ở port; không dùng `Optional` làm field hay tham số.
- Stream kết thúc bằng `.toList()`; `List.of`/`Map.of`; `switch` dạng mũi tên; text block cho JPQL dài.
- Status/type là **enum**, parse ở biên.
- Hằng số có tên cho literal lặp, múi giờ nghiệp vụ, kích thước trang.
- Exception nghiệp vụ là unchecked, đặt trong `domain/exception`, mang mã lỗi; map sang HTTP ở `@RestControllerAdvice`.
- Log: `@Slf4j`, placeholder `{}`, không nối chuỗi; lỗi kèm exception ở tham số cuối.
- Import từng class, không wildcard.

## Comment
- Javadoc cho class và method public của port/service: nói **vì sao** và ràng buộc nghiệp vụ.
- Comment `//` là văn xuôi thuần, không token code, không `;` cuối dòng (xem `sonar.md`).
- Không để code chết, TODO không có người/việc cụ thể.

## Commit
- `feat(scope): ...`, `fix(scope): ...`, `refactor(scope): ...`, `test(scope): ...`.
